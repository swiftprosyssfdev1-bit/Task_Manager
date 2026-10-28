import os
from decimal import Decimal

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Q
from django.utils import timezone

from accounts.forms import BootstrapMixin
from accounts.models import User
from projects.models import Project

from .attachments import AttachmentFormMixin
from .models import (Task, TaskAttachment, TaskComment, TaskCommentAttachment, TaskWorkLog, Technology,
                     sum_hours)


def _date_widget():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _time_widget():
    return forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


NOTE_LABEL = "Note for history (optional)"


def colour_priority(field):
    """Marks the priority dropdown so CSS/JS colour it: Low green, Medium yellow, High orange, Critical red."""
    field.widget.attrs["class"] = f"{field.widget.attrs.get('class', '')} priority-select".strip()


class TaskForm(AttachmentFormMixin, BootstrapMixin, forms.ModelForm):
    """Manager / admin form: full control over assignment and deadlines."""
    change_note = forms.CharField(required=False, label=NOTE_LABEL, max_length=500,
                                  help_text="Saved with any status or deadline change, e.g. the reason for a new date.")

    attachment_model = TaskAttachment
    attachment_fk = "task"
    field_order = ["title", "description", "project", "assigned_to", "priority", "status",
                   "assign_date", "est_date", "end_date", "remarks", "attachments", "change_note"]

    class Meta:
        model = Task
        fields = ["title", "description", "project", "assigned_to", "priority", "status",
                  "assign_date", "est_date", "end_date", "remarks"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4}), "remarks": forms.Textarea(attrs={"rows": 2}),
                   "assign_date": _date_widget(), "est_date": _date_widget(), "end_date": _date_widget()}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        colour_priority(self.fields["priority"])
        self._add_attachment_field()
        self.fields["project"].queryset = Project.objects.visible_to(user).filter(is_active=True)
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].queryset = user.employees_in_scope().filter(
                is_active=True).order_by("first_name", "username")

    def clean(self):
        cleaned = super().clean()
        project, employee = cleaned.get("project"), cleaned.get("assigned_to")
        if project and employee and not project.memberships.filter(user=employee).exists():
            self.add_error("assigned_to", f"{employee.display_name} is not a member of {project.name}.")
        return cleaned


class TaskCreateForm(TaskForm):
    """Manager creates a (daily) task for one project and assigns it to one or more team members."""
    assignees = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label="Assign to",
        widget=forms.SelectMultiple(attrs={
            "class": "form-select multi-select-dropdown",
            "data-placeholder": "Select team members...",
            "data-field": "assignees",
        }),
        help_text="Select one employee, or several. One task is created for each. Only the project's team is shown.")

    class Meta(TaskForm.Meta):
        fields = ["title", "description", "project", "priority", "status", "assign_date", "est_date", "end_date",
                  "remarks"]

    field_order = ["project", "assignees", "title", "description", "priority", "status",
                   "assign_date", "est_date", "end_date", "remarks", "attachments", "change_note"]

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        mine = Project.objects.managed_by(user).filter(is_active=True)
        self.fields["project"].queryset = mine.select_related("department")
        self.fields["project"].required = True
        self.fields["assignees"].queryset = user.employees_in_scope().filter(
            is_active=True, project_memberships__project__in=mine).distinct().order_by("first_name", "username")
        self.fields["status"].choices = [(Task.Status.NOT_STARTED, "Not Started")]
        self.fields["status"].initial = Task.Status.NOT_STARTED
        today = timezone.localdate()
        self.initial.setdefault("assign_date", today)
        self.initial.setdefault("est_date", today)  # daily task: due the same day unless changed

    def clean(self):
        cleaned = forms.ModelForm.clean(self)
        project, people = cleaned.get("project"), cleaned.get("assignees")
        if project and people:
            member_ids = set(project.memberships.values_list("user_id", flat=True))
            for emp in people:
                if emp.pk not in member_ids:
                    self.add_error("assignees", f"{emp.display_name} is not a member of {project.name}. Add them to the project team first.")
        return cleaned


class TaskEmployeeForm(BootstrapMixin, forms.ModelForm):
    """Employee form: status, technologies, backup and remarks only. Everything else is manager-controlled."""
    change_note = forms.CharField(required=False, label=NOTE_LABEL, max_length=500)

    class Meta:
        model = Task
        fields = ["status", "technologies", "backup", "remarks"]
        widgets = {"backup": forms.Textarea(attrs={"rows": 2}), "remarks": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        S = Task.Status
        allowed = [S.NOT_STARTED, S.IN_PROGRESS, S.ON_HOLD, S.BLOCKED,
                   S.PENDING_REVIEW if settings.REQUIRE_MANAGER_REVIEW else S.COMPLETED]
        current = self.instance.status
        self.fields["status"].choices = [c for c in S.choices if c[0] in allowed or c[0] == current]
        if current in Task.TERMINAL_STATUSES:
            self.fields["status"].disabled = True
        if not self.instance.uses_technologies:
            del self.fields["technologies"]  # the task's department does not record technologies
            return
        techs = Q(is_active=True)
        if self.instance.pk:
            techs |= Q(tasks=self.instance)
        self.fields["technologies"].queryset = Technology.objects.filter(techs).distinct()
        self.fields["technologies"].help_text = "Hold Ctrl (Cmd on Mac) to select several."


class TaskFilterForm(BootstrapMixin, forms.Form):
    ORDERING = [("-created_at", "Newest first"), ("est_date", "Deadline: soonest"),
                ("-est_date", "Deadline: latest"), ("assign_date", "Assigned: oldest"),
                ("-assign_date", "Assigned: newest"), ("status", "Status")]

    q = forms.CharField(required=False, label="Search")
    employee = forms.ModelChoiceField(User.objects.none(), required=False)
    project = forms.ModelChoiceField(Project.objects.none(), required=False)
    status = forms.ChoiceField(choices=[("", "All statuses")] + Task.Status.choices, required=False)
    priority = forms.ChoiceField(choices=[("", "All priorities")] + Task.Priority.choices, required=False)
    overdue = forms.BooleanField(required=False, label="Overdue only")
    ordering = forms.ChoiceField(choices=ORDERING, required=False)

    def __init__(self, *args, user, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        colour_priority(self.fields["priority"])
        p_qs = Project.objects.visible_to(user)
        if branch and user.is_admin_role:
            p_qs = p_qs.filter(branch=branch)
        self.fields["project"].queryset = p_qs
        if user.can_manage_tasks:
            e_qs = user.employees_in_scope().order_by("first_name", "username")
            if branch and user.is_admin_role:
                e_qs = e_qs.filter(branch=branch)
            self.fields["employee"].queryset = e_qs
        else:
            del self.fields["employee"]


class WorkLogForm(BootstrapMixin, forms.ModelForm):
    """The instance must arrive with task and employee already set."""

    class Meta:
        model = TaskWorkLog
        fields = ["work_date", "start_time", "end_time", "duration_hours", "description",
                  "technologies", "remarks", "attachment"]
        widgets = {"work_date": _date_widget(), "start_time": _time_widget(), "end_time": _time_widget(),
                   "description": forms.Textarea(attrs={"rows": 3}), "remarks": forms.Textarea(attrs={"rows": 2})}
        labels = {"duration_hours": "Hours worked"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["duration_hours"].required = False
        self.fields["duration_hours"].help_text = "Leave blank to calculate from the start and end time."
        if self.instance.task.uses_technologies:
            self.fields["technologies"].queryset = Technology.objects.filter(is_active=True)
            self.fields["technologies"].help_text = "Hold Ctrl (Cmd on Mac) to select several."
        else:
            del self.fields["technologies"]  # the task's department does not record technologies
        if not self.initial.get("work_date"):
            self.initial["work_date"] = timezone.localdate()

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if isinstance(f, UploadedFile):
            ext = os.path.splitext(f.name)[1].lower().lstrip(".")
            if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
                raise ValidationError("Allowed file types: " + ", ".join(settings.ALLOWED_UPLOAD_EXTENSIONS) + ".")
            if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise ValidationError(f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
        return f

    def clean(self):
        d = super().clean()
        task, employee_id = self.instance.task, self.instance.employee_id
        work_date, start, end, dur = d.get("work_date"), d.get("start_time"), d.get("end_time"), d.get("duration_hours")

        if bool(start) != bool(end):
            self.add_error("end_time", "Enter both a start and an end time, or leave both blank.")
        elif start and end:
            if end <= start:
                self.add_error("end_time", "End time must be after the start time.")
            else:
                minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
                dur = d["duration_hours"] = (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))
        elif dur is None and "duration_hours" not in self.errors:
            self.add_error("duration_hours", "Enter the hours worked, or a start and end time.")

        if dur is not None and not (Decimal("0") < dur <= Decimal("24")):
            self.add_error("duration_hours", "Hours must be more than 0 and at most 24.")
            dur = None

        if work_date:
            if work_date > timezone.localdate():
                self.add_error("work_date", "Work date cannot be in the future.")
            elif work_date < task.assign_date:
                self.add_error("work_date", "Work date cannot be before the task was assigned.")
            elif dur is not None:
                others = TaskWorkLog.objects.filter(employee_id=employee_id, work_date=work_date).exclude(
                    pk=self.instance.pk)
                if others.aggregate(t=sum_hours())["t"] + dur > 24:
                    self.add_error("duration_hours", "Logged time for this date would be more than 24 hours.")
                desc = (d.get("description") or "").strip()
                if desc and others.filter(task=task, duration_hours=dur, description__iexact=desc).exists():
                    raise ValidationError("This looks like a duplicate of an existing entry for the same day.")
        return d


class WorkLogCorrectionForm(WorkLogForm):
    reason = forms.CharField(label="Reason for correction", max_length=500)


class CommentForm(AttachmentFormMixin, BootstrapMixin, forms.ModelForm):
    attachment_model = TaskCommentAttachment
    attachment_fk = "comment"

    class Meta:
        model = TaskComment
        fields = ["body"]
        labels = {"body": "Add a comment"}
        widgets = {"body": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].required = False  # a comment may be just documents
        self._add_attachment_field()
        self.fields["attachments"].label = "Attach documents"

    def clean(self):
        d = super().clean()
        if not (d.get("body") or "").strip() and not d.get("attachments") and not self.errors:
            self.add_error("body", "Write a comment or attach a document before posting.")
        return d


class ReviewForm(BootstrapMixin, forms.Form):
    APPROVE, REWORK = "approve", "rework"
    decision = forms.ChoiceField(choices=[(APPROVE, "Approve and mark completed"), (REWORK, "Send back for rework")])
    feedback = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        d = super().clean()
        if d.get("decision") == self.REWORK and not (d.get("feedback") or "").strip():
            self.add_error("feedback", "Explain what needs to change.")
        return d


class TechnologyForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Technology
        fields = ["name", "is_active"]
