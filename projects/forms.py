from django import forms
from django.db import transaction
from django.db.models import Q

from accounts.forms import BootstrapMixin
from accounts.models import User

from tasks.attachments import AttachmentFormMixin
from tasks.models import Technology

from .models import Department, Project, ProjectAttachment, ProjectMembership


class DepartmentForm(BootstrapMixin, forms.ModelForm):
    branch = forms.ChoiceField(
        choices=[("", "All branches / Head Office")] + list(User.Branch.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"})
    )
    managers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(role=User.Role.MANAGER, is_active=True).order_by("first_name", "username"),
        required=False,
        label="Managers",
        widget=forms.SelectMultiple(attrs={"class": "form-select multi-select-dropdown", "data-placeholder": "Select managers..."}),
        help_text="Managers who run this department. Multiple managers can be added."
    )

    class Meta:
        model = Department
        fields = ["name", "branch", "description", "uses_technologies", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["managers"].initial = list(
                self.instance.staff.filter(role=User.Role.MANAGER).values_list("pk", flat=True)
            )

    @transaction.atomic
    def save(self, commit=True):
        dept = super().save(commit)
        if commit:
            branch_changed = "branch" in self.changed_data
            if branch_changed:
                dept.staff.update(branch=dept.branch)
                dept.projects.update(branch=dept.branch)
            if "managers" in self.data:
                selected_managers = set(self.cleaned_data.get("managers") or [])
                current_managers = set(dept.staff.filter(role=User.Role.MANAGER))
                for m in selected_managers - current_managers:
                    m.department = dept
                    m.branch = dept.branch
                    m.save(update_fields=["department", "branch"])
                for m in current_managers - selected_managers:
                    m.department = None
                    m.save(update_fields=["department"])
        return dept


class ProjectForm(AttachmentFormMixin, BootstrapMixin, forms.ModelForm):
    attachment_model = ProjectAttachment
    attachment_fk = "project"
    team_members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False, label="Team members",
        widget=forms.SelectMultiple(attrs={"class": "form-select multi-select-dropdown", "data-placeholder": "Select team members..."}),
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
            # A manager's projects always belong to the manager's own department and branch.
            if "department" in self.fields:
                del self.fields["department"]
            if "branch" in self.fields:
                del self.fields["branch"]
            if not self.instance.pk:
                self.instance.department_id = user.department_id
                self.instance.branch = (user.department.branch if user.department else "") or user.branch or ""
                self.instance.manager = user
            q = user.employees_in_scope()
            if self.instance.pk:
                q = q | self.instance.members.all()
            self.fields["team_members"].queryset = q.filter(is_active=True).distinct().order_by(
                "first_name", "username")
        else:
            if "branch" in self.fields:
                del self.fields["branch"]
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
        if not self.instance.branch:
            if self.instance.department and self.instance.department.branch:
                self.instance.branch = self.instance.department.branch
            elif self.user and getattr(self.user, "branch", None):
                self.instance.branch = self.user.branch
        project = super().save(commit)
        if commit:
            wanted = {u.pk for u in self.cleaned_data["team_members"]}
            existing = set(project.memberships.values_list("user_id", flat=True))
            project.memberships.filter(user_id__in=existing - wanted).delete()
            ProjectMembership.objects.bulk_create(
                [ProjectMembership(project=project, user_id=pk) for pk in wanted - existing])
            self.save_attachments_to([project], self.user)
        return project
