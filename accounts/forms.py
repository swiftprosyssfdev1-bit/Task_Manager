from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q

from .models import User


class BootstrapMixin:
    """Adds Bootstrap 5 classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxSelectMultiple):
                continue  # styled by .check-grid in app.css
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "form-select"
            else:
                css = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


def _active_departments(current=None):
    from projects.models import Department
    qs = Department.objects.filter(is_active=True)
    if current:
        qs = Department.objects.filter(Q(is_active=True) | Q(pk=current))
    return qs


class ManagerSelect(forms.Select):
    """Tags each manager option with its department so the form can narrow the list when a department is chosen."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-department"] = instance.department_id or ""
        return option


class ManagerChoiceField(forms.ModelChoiceField):
    widget = ManagerSelect

    def label_from_instance(self, obj):
        return f"{obj.display_name} ({obj.department.name})" if obj.department_id else obj.display_name


class AssignmentMixin:
    """Admin-side fields shared by create and edit: every account gets a department; an employee also gets the
    manager they report to, and that manager must belong to the chosen department."""

    def setup_assignment(self, role, current_department=None, current_manager=None):
        self.role = role
        dept = self.fields["department"]
        dept.queryset = _active_departments(current_department)
        dept.empty_label = "Select department"
        dept.required = True
        if role == User.Role.MANAGER:
            del self.fields["manager"]
            dept.help_text = "The manager will run this department's employees, projects and reports."
            return
        boss = self.fields["manager"] = ManagerChoiceField(
            queryset=User.objects.filter(role=User.Role.MANAGER).filter(
                Q(is_active=True) | Q(pk=current_manager)).exclude(department=None).select_related("department")
            .order_by("first_name", "username"),
            empty_label="Select manager", label="Reports to (manager)", required=True,
            widget=ManagerSelect(attrs={"class": "form-select"}))
        boss.help_text = "Only this manager can assign tasks to the employee and see their reports."
        # Keep the field order department -> manager -> the rest.
        order = [n for n in self.fields if n not in ("department", "manager")]
        self.order_fields(["department", "manager"] + order)

    def clean(self):
        cleaned = super().clean()
        boss, dept = cleaned.get("manager"), cleaned.get("department")
        if boss and dept and boss.department_id != dept.pk:
            self.add_error("manager", f"{boss.display_name} belongs to {boss.department}, not {dept}. "
                                      "Pick a manager from the same department.")
        return cleaned


class UserCreateForm(AssignmentMixin, BootstrapMixin, UserCreationForm):
    """Admin adds managers and employees. Django's strength rules are skipped for the initial password;
    they still apply to password reset."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("department", "manager", "username", "first_name", "last_name", "email", "employee_code",
                  "designation")

    def __init__(self, *args, role=User.Role.EMPLOYEE, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_assignment(role)
        self.fields["password1"].help_text = "Any password is accepted. Ask the person to change it after first sign-in."
        if "manager" in self.fields:  # BootstrapMixin ran before the field existed
            self.fields["manager"].widget.attrs["class"] = "form-select"

    def _post_clean(self):
        # Run the normal model validation but not password_validation.validate_password().
        forms.ModelForm._post_clean(self)

    def save(self, commit=True):
        self.instance.role = self.role
        return super().save(commit)


class UserUpdateForm(AssignmentMixin, BootstrapMixin, forms.ModelForm):
    """Admin edits a manager or an employee, including their department and (for employees) their manager."""

    class Meta:
        model = User
        fields = ("department", "manager", "first_name", "last_name", "email", "employee_code", "designation",
                  "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_assignment(self.instance.role, self.instance.department_id, self.instance.manager_id)
        if "manager" in self.fields:
            self.fields["manager"].widget.attrs["class"] = "form-select"

    def save(self, commit=True):
        user = super().save(commit)
        if commit and user.is_manager_role and "department" in self.changed_data:
            # A manager's team follows them into the new department.
            user.team.update(department=user.department)
        return user
