from django import forms
from django.db import transaction
from django.db.models import Q

from accounts.forms import BootstrapMixin
from accounts.models import User

from tasks.attachments import AttachmentFormMixin
from tasks.models import Technology

from .models import Department, Project, ProjectAttachment, ProjectMembership


class DepartmentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "description", "uses_technologies", "is_active"]


class ProjectForm(AttachmentFormMixin, BootstrapMixin, forms.ModelForm):
    attachment_model = ProjectAttachment
    attachment_fk = "project"
    team_members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False, label="Team members", widget=forms.CheckboxSelectMultiple,
        help_text="Employees of your department who will work on this project. Tasks can only be assigned to team members.")

    class Meta:
        model = Project
        fields = ["department", "name", "code", "description", "technologies", "start_date", "end_date", "est_date", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "technologies": forms.CheckboxSelectMultiple,
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "est_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self._add_attachment_field()
        if user is not None:
            # A manager's projects always belong to the manager's own department and use its employees only.
            del self.fields["department"]
            if not self.instance.pk:
                self.instance.department_id = user.department_id
            self.fields["team_members"].queryset = user.employees_in_scope().filter(is_active=True).order_by(
                "first_name", "username")
        else:
            self.fields["team_members"].queryset = User.objects.filter(
                role=User.Role.EMPLOYEE, is_active=True).order_by("first_name", "username")
            self._init_department_field()
        if user is not None and not user.tracks_technologies:
            del self.fields["technologies"]  # this department does not record technologies
        else:
            self.fields["technologies"].queryset = Technology.objects.filter(is_active=True)
            self.fields["technologies"].required = False
            self.fields["technologies"].help_text = "Technologies the team must use. Missing one? Add it under Technologies in the sidebar."
        if self.instance.pk:
            self.fields["team_members"].initial = list(
                self.instance.memberships.values_list("user_id", flat=True))

    def _init_department_field(self):
        # Keep the current department selectable even if it was deactivated later.
        dept_qs = Department.objects.filter(is_active=True)
        if self.instance.pk and self.instance.department_id:
            dept_qs = Department.objects.filter(Q(is_active=True) | Q(pk=self.instance.department_id))
        self.fields["department"].queryset = dept_qs
        self.fields["department"].required = True
        self.fields["department"].empty_label = "Select department"

    def clean(self):
        cleaned = super().clean()
        start, est = cleaned.get("start_date"), cleaned.get("est_date")
        if start and est and est < start:
            self.add_error("est_date", "Est. date cannot be before the start date.")
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        project = super().save(commit)
        if commit:
            wanted = {u.pk for u in self.cleaned_data["team_members"]}
            existing = set(project.memberships.values_list("user_id", flat=True))
            project.memberships.filter(user_id__in=existing - wanted).delete()
            ProjectMembership.objects.bulk_create(
                [ProjectMembership(project=project, user_id=pk) for pk in wanted - existing])
            self.save_attachments_to([project], self.user)
        return project
