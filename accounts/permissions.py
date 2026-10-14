from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from .models import User

ADMIN_ONLY = (User.Role.ADMIN,)
MANAGER_ONLY = (User.Role.MANAGER,)
STAFF_ROLES = (User.Role.ADMIN, User.Role.MANAGER)


class RoleRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Server-side role check. Anonymous users go to login; wrong role gets 403."""
    allowed_roles = ()

    def test_func(self):
        return self.request.user.role in self.allowed_roles

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()


class TitleMixin:
    title = ""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.title
        return ctx
