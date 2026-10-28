from django import forms

from accounts.forms import BootstrapMixin
from accounts.models import User
from projects.models import Project


def _date():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class AnalyticsFilterForm(BootstrapMixin, forms.Form):
    start = forms.DateField(label="From", required=False, widget=_date())
    end = forms.DateField(label="To", required=False, widget=_date())
    project = forms.ModelChoiceField(Project.objects.none(), required=False, empty_label="All projects")
    employee = forms.ModelChoiceField(User.objects.none(), required=False, empty_label="All employees")

    def __init__(self, *args, user, show_employee=True, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        p_qs = Project.objects.visible_to(user)
        if branch and user.is_admin_role:
            p_qs = p_qs.filter(branch=branch)
        self.fields["project"].queryset = p_qs
        if show_employee:
            e_qs = user.employees_in_scope().order_by("first_name", "username")
            if branch and user.is_admin_role:
                e_qs = e_qs.filter(branch=branch)
            self.fields["employee"].queryset = e_qs
        else:
            del self.fields["employee"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start") and cleaned.get("end") and cleaned["end"] < cleaned["start"]:
            self.add_error("end", "End date cannot be before the start date.")
        return cleaned
