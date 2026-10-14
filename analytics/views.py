from collections import Counter

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.models import User
from accounts.permissions import STAFF_ROLES, RoleRequiredMixin
from projects.models import Project
from tasks.models import Task, Technology

from . import services
from .forms import AnalyticsFilterForm


def _period(form, today):
    """Defaults to the current month; a bad or empty filter falls back to the default."""
    start, end = today.replace(day=1), today
    if form.is_valid():
        start = form.cleaned_data.get("start") or start
        end = form.cleaned_data.get("end") or end
    if end < start:
        start, end = today.replace(day=1), today
    return start, end


class TeamAnalyticsView(RoleRequiredMixin, TemplateView):
    """Employee and project statistics for the people and projects the user manages."""
    allowed_roles = STAFF_ROLES
    template_name = "analytics/team.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user, today = self.request.user, timezone.localdate()
        form = AnalyticsFilterForm(self.request.GET or None, user=user)
        start, end = _period(form, today)
        tasks = Task.objects.visible_to(user).filter(is_archived=False)
        if form.is_valid():
            if form.cleaned_data.get("project"):
                tasks = tasks.filter(project=form.cleaned_data["project"])
            if form.cleaned_data.get("employee"):
                tasks = tasks.filter(assigned_to=form.cleaned_data["employee"])
        ctx.update(filter_form=form, start=start, end=end,
                   employees=services.employee_stats(tasks, start, end, services.get_holidays(), today),
                   projects=services.project_stats(tasks, today),
                   workload_threshold=services.workload_threshold())
        return ctx


class EmployeeReportView(LoginRequiredMixin, TemplateView):
    """Individual report. Staff see employees in their scope; an employee sees only their own."""
    template_name = "analytics/employee_report.html"

    def get_employee(self):
        user, pk = self.request.user, self.kwargs["pk"]
        if user.is_employee_role and user.pk != pk:
            raise Http404
        employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE)
        if user.is_manager_role:
            if employee.manager_id != user.pk:  # a manager only sees reports of the employees assigned to them
                raise Http404
        return employee

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user, today = self.request.user, timezone.localdate()
        employee = self.get_employee()
        form = AnalyticsFilterForm(self.request.GET or None, user=user, show_employee=False)
        start, end = _period(form, today)
        holidays = services.get_holidays()
        tasks = Task.objects.visible_to(user).filter(is_archived=False, assigned_to=employee)
        if form.is_valid() and form.cleaned_data.get("project"):
            tasks = tasks.filter(project=form.cleaned_data["project"])
        stats = services.employee_stats(tasks, start, end, holidays, today, ensure_users=[employee])[0]
        completed = services.completed_metrics(tasks, start, end, holidays)
        technologies = (Technology.objects.filter(tasks__in=[m.task.pk for m in completed])
                        .annotate(n=Count("tasks")).order_by("-n", "name"))
        open_tasks = tasks.filter(status__in=Task.OPEN_STATUSES)
        by_status = Counter(open_tasks.values_list("status", flat=True))
        by_priority = Counter(open_tasks.values_list("priority", flat=True))
        ctx.update(
            employee=employee, filter_form=form, start=start, end=end, stats=stats, completed=completed,
            technologies=technologies,
            projects=Project.objects.visible_to(user).filter(memberships__user=employee).select_related("manager"),
            workload_status=[(label, by_status.get(v, 0)) for v, label in Task.Status.choices
                             if v in Task.OPEN_STATUSES],
            workload_priority=[(label, by_priority.get(v, 0)) for v, label in Task.Priority.choices],
            history=services.annotate_tasks(tasks.select_related("project")).order_by("-created_at")[:50],
            charts={"status": services.status_breakdown(tasks), "trend": services.completion_trend(tasks, today),
                    "estimate": services.estimate_vs_actual(tasks, 12)},
            is_self=user.pk == employee.pk)
        return ctx
