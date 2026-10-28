import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from analytics.services import dashboard_data
from notifications import services as notifications
from projects.models import Department, Project
from tasks.models import Task

from .forms import UserCreateForm, UserUpdateForm
from .models import User
from .permissions import RoleRequiredMixin, TitleMixin


LIST_URL = {User.Role.MANAGER: "manager_list", User.Role.EMPLOYEE: "user_list"}


class SetActiveBranchView(LoginRequiredMixin, View):
    def get(self, request):
        if request.user.is_admin_role:
            branch = request.GET.get("branch", "").strip()
            request.session["active_branch"] = branch
        next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/"
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        clean_url = re.sub(r'([?&])branch=[^&]*(&|$)', r'\1', next_url).rstrip('?&')
        if not clean_url:
            clean_url = "/"
        return redirect(clean_url)


def managed_accounts(user, role):
    """Accounts of `role` this user may open: admin -> all of them, manager -> only their own employees."""
    qs = User.objects.filter(role=role).select_related("department", "manager")
    if user.is_admin_role:
        return qs
    if user.is_manager_role and role == User.Role.EMPLOYEE:
        return qs.filter(manager_id=user.pk)
    return qs.none()


class AccountPageMixin(RoleRequiredMixin):
    """Admin runs both account pages. A manager may only look at their own employees (read-only)."""
    write_access = True

    @property
    def account_role(self):
        return self.kwargs.get("role", User.Role.EMPLOYEE)

    def test_func(self):
        user = self.request.user
        if user.is_admin_role:
            return True
        return (not self.write_access) and user.is_manager_role and self.account_role == User.Role.EMPLOYEE

    def get_success_url(self):
        return reverse(LIST_URL[self.account_role])


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not self.request.user.is_admin_role:
            notifications.sweep_overdue_if_due()  # keeps "overdue" alerts current without needing a scheduler
        if self.request.user.is_manager_role:
            notifications.notify_unassigned_employees(self.request.user)
            ctx["unassigned_employees"] = notifications.unassigned_employees_for_manager(self.request.user)
        selected_branch = ""
        if self.request.user.is_admin_role:
            if "branch" in self.request.GET:
                selected_branch = self.request.GET.get("branch", "").strip()
                self.request.session["active_branch"] = selected_branch
            else:
                selected_branch = self.request.session.get("active_branch", "")
            ctx["selected_branch"] = selected_branch
            ctx["branches"] = User.Branch.choices
            branch_cards = []
            for branch_val, branch_label in User.Branch.choices:
                if selected_branch and branch_val != selected_branch:
                    continue
                mgrs = list(User.objects.filter(role=User.Role.MANAGER, branch=branch_val, is_active=True).order_by("first_name", "username"))
                emp_count = User.objects.filter(role=User.Role.EMPLOYEE, branch=branch_val, is_active=True).count()
                proj_count = Project.objects.filter(branch=branch_val, is_active=True).count()
                task_count = Task.objects.filter(
                    Q(assigned_to__branch=branch_val) | Q(project__branch=branch_val),
                    is_archived=False
                ).distinct().count()
                branch_cards.append({
                    "name": branch_label,
                    "code": branch_val,
                    "managers": mgrs,
                    "manager_count": len(mgrs),
                    "employee_count": emp_count,
                    "project_count": proj_count,
                    "task_count": task_count,
                    "add_manager_url": reverse("manager_create") + f"?branch={branch_val}",
                    "add_employee_url": reverse("user_create") + f"?branch={branch_val}",
                    "view_managers_url": reverse("manager_list") + f"?branch={branch_val}",
                    "view_projects_url": reverse("project_list") + f"?branch={branch_val}",
                })
            ctx["branch_cards"] = branch_cards
        ctx.update(dashboard_data(self.request.user, branch=selected_branch if self.request.user.is_admin_role else None))
        qs = Task.objects.visible_to(self.request.user).filter(is_archived=False)
        if self.request.user.is_admin_role and selected_branch:
            qs = qs.filter(Q(assigned_to__branch=selected_branch) | Q(project__branch=selected_branch))
        ctx["recent_tasks"] = qs.select_related("project", "assigned_to").prefetch_related("technologies").with_hours().order_by("-created_at")[:8]
        return ctx


class UserListView(AccountPageMixin, ListView):
    write_access = False
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        qs = managed_accounts(self.request.user, self.account_role).order_by("first_name", "username")
        if self.request.user.is_admin_role:
            dept = self.request.GET.get("department", "")
            if dept.isdigit():
                qs = qs.filter(department_id=int(dept))
            if "branch" in self.request.GET:
                branch = self.request.GET.get("branch", "").strip()
                self.request.session["active_branch"] = branch
            else:
                branch = self.request.session.get("active_branch", "")
            if branch:
                qs = qs.filter(branch=branch)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q)
                           | Q(last_name__icontains=q) | Q(email__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["querystring"] = params.urlencode()
        ctx["account_role"] = self.account_role
        ctx["role_label"] = User.Role(self.account_role).label
        ctx["departments"] = Department.objects.all() if self.request.user.is_admin_role else []
        ctx["selected_department"] = self.request.GET.get("department", "")
        branch = self.request.GET.get("branch")
        if branch is None and self.request.user.is_admin_role:
            branch = self.request.session.get("active_branch", "")
        ctx["branches"] = User.Branch.choices if self.request.user.is_admin_role else []
        ctx["selected_branch"] = branch or ""
        return ctx


class UserCreateView(AccountPageMixin, SuccessMessageMixin, CreateView):
    form_class = UserCreateForm
    template_name = "form.html"

    @property
    def title(self):
        return f"Add {self.account_role}"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title
        return ctx

    def get_success_message(self, cleaned_data):
        return f"{User.Role(self.account_role).label} created."

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and user.is_admin_role:
            if not Department.objects.filter(is_active=True).exists():
                messages.info(request, "Add a department first, then add its manager.")
                return redirect("department_create")
            if kwargs.get("role", User.Role.EMPLOYEE) == User.Role.EMPLOYEE and not User.objects.filter(
                    role=User.Role.MANAGER, is_active=True, department__isnull=False).exists():
                messages.info(request, "Add a manager under a department first, then add employees to that manager.")
                return redirect("manager_create")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        get = self.request.GET
        if get.get("manager", "").isdigit():
            boss = User.objects.filter(pk=int(get["manager"]), role=User.Role.MANAGER).first()
            if boss:
                initial["manager"], initial["department"] = boss.pk, boss.department_id
        if get.get("department", "").isdigit():
            initial.setdefault("department", int(get["department"]))
        if get.get("branch", ""):
            initial.setdefault("branch", get["branch"])
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["role"] = self.account_role
        return kwargs


class UserUpdateView(AccountPageMixin, TitleMixin, SuccessMessageMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "form.html"
    success_message = "Account updated."
    # Never "user": that name belongs to the signed-in user (sidebar, permissions in templates).
    context_object_name = "account"

    @property
    def title(self):
        return f"Edit {self.account_role}"

    def get_queryset(self):
        return managed_accounts(self.request.user, self.account_role)


class UserToggleActiveView(AccountPageMixin, View):
    def post(self, request, pk, role=User.Role.EMPLOYEE):
        user = get_object_or_404(managed_accounts(request.user, role), pk=pk)
        if user.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
        else:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            messages.success(request, f"{user.display_name} is now {'active' if user.is_active else 'deactivated'}.")
        return redirect(LIST_URL[role])
