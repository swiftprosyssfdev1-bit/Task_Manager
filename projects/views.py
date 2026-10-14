from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Prefetch, Q
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.models import User
from accounts.permissions import ADMIN_ONLY, MANAGER_ONLY, RoleRequiredMixin, TitleMixin
from tasks.models import Task

from .forms import DepartmentForm, ProjectForm
from .models import Department, Project


class ProjectListView(LoginRequiredMixin, ListView):
    template_name = "projects/project_list.html"
    context_object_name = "projects"
    paginate_by = 20

    def get_queryset(self):
        live = Q(tasks__is_archived=False)
        qs = Project.objects.visible_to(self.request.user)
        dept = self.request.GET.get("department", "")
        if dept.isdigit():
            qs = qs.filter(department_id=int(dept))
        return (qs.select_related("manager", "department").prefetch_related("technologies")
                .annotate(task_total=Count("tasks", filter=live, distinct=True),
                          task_done=Count("tasks", filter=live & Q(tasks__status=Task.Status.COMPLETED),
                                          distinct=True))
                .order_by("department__name", "name"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["departments"] = Department.objects.all() if user.is_admin_role else Department.objects.none()
        ctx["selected_department"] = self.request.GET.get("department", "")
        return ctx


class ProjectDetailView(LoginRequiredMixin, DetailView):
    template_name = "projects/project_detail.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.visible_to(self.request.user).select_related("manager", "department")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["members"] = self.object.members.order_by("first_name", "username")
        qs = (Task.objects.visible_to(self.request.user).filter(project=self.object, is_archived=False)
              .select_related("assigned_to").prefetch_related("technologies").with_hours())
        ctx["tasks"] = qs.order_by("-created_at")[:50]
        ctx["technologies"] = self.object.technologies.all()
        ctx.update(self.duration_stats(list(qs)))
        return ctx

    def duration_stats(self, tasks):
        """How many days work is taking, per employee and for the whole project."""
        today = timezone.localdate()
        rows = {}
        for t in tasks:
            r = rows.setdefault(t.assigned_to_id, {"name": t.assigned_to.display_name, "total": 0, "done": 0,
                                                   "open": 0, "days": [], "hours": 0})
            r["total"] += 1
            r["hours"] += t.logged_hours or 0
            if t.status == Task.Status.COMPLETED:
                r["done"] += 1
                r["days"].append(t.days_taken)
            elif t.status in Task.OPEN_STATUSES:
                r["open"] += 1
        team = []
        for r in sorted(rows.values(), key=lambda x: x["name"].lower()):
            r["avg_days"] = round(sum(r["days"]) / len(r["days"]), 1) if r["days"] else None
            r["max_days"] = max(r["days"]) if r["days"] else None
            team.append(r)
        all_days = [d for r in rows.values() for d in r["days"]]
        begun = self.object.start_date or min((t.assign_date for t in tasks), default=None)
        return {"team_stats": team,
                "avg_days": round(sum(all_days) / len(all_days), 1) if all_days else None,
                "days_running": max(1, (today - begun).days + 1) if begun and begun <= today else None,
                "done_count": sum(r["done"] for r in rows.values()),
                "task_count": sum(r["total"] for r in rows.values())}


class ProjectCreateView(RoleRequiredMixin, TitleMixin, SuccessMessageMixin, CreateView):
    allowed_roles = MANAGER_ONLY
    model = Project
    form_class = ProjectForm
    template_name = "form.html"
    title = "New project"
    success_message = "Project created."

    def dispatch(self, request, *args, **kwargs):
        # Departments are set up by the admin. A manager without one cannot create projects yet.
        user = request.user
        if user.is_authenticated and user.can_assign_tasks and not (
                user.department_id and user.department.is_active):
            messages.error(request, "You are not assigned to an active department. Ask the admin to assign you to one.")
            return redirect("project_list")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.manager = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("project_detail", args=[self.object.pk])


class ProjectUpdateView(RoleRequiredMixin, TitleMixin, SuccessMessageMixin, UpdateView):
    allowed_roles = MANAGER_ONLY
    model = Project
    form_class = ProjectForm
    template_name = "form.html"
    title = "Edit project"

    def get_queryset(self):
        return Project.objects.managed_by(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    success_message = "Project updated."

    def get_success_url(self):
        return reverse("project_detail", args=[self.object.pk])


class DepartmentListView(RoleRequiredMixin, ListView):
    allowed_roles = ADMIN_ONLY
    template_name = "projects/department_list.html"
    context_object_name = "departments"

    def get_queryset(self):
        return Department.objects.prefetch_related(
            Prefetch("staff", queryset=User.objects.filter(role=User.Role.MANAGER).order_by("first_name", "username"),
                     to_attr="managers")
        ).annotate(
            project_count=Count("projects", distinct=True),
            task_count=Count("projects__tasks", filter=Q(projects__tasks__is_archived=False), distinct=True),
            employee_count=Count("staff", filter=Q(staff__role=User.Role.EMPLOYEE), distinct=True))


class DepartmentCreateView(RoleRequiredMixin, TitleMixin, SuccessMessageMixin, CreateView):
    allowed_roles = ADMIN_ONLY
    form_class = DepartmentForm
    template_name = "form.html"
    title = "Add department"
    success_url = reverse_lazy("department_list")
    success_message = "Department added. Now add a manager under it."


class DepartmentUpdateView(RoleRequiredMixin, TitleMixin, SuccessMessageMixin, UpdateView):
    allowed_roles = ADMIN_ONLY
    model = Department
    form_class = DepartmentForm
    template_name = "form.html"
    title = "Edit department"
    success_url = reverse_lazy("department_list")
    success_message = "Department updated."
