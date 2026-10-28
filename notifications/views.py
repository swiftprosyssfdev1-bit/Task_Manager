from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from accounts.models import User
from accounts.permissions import RoleRequiredMixin

from . import services
from .models import Notification

NOTIFIED_ROLES = (User.Role.MANAGER, User.Role.EMPLOYEE)  # the admin is view-only, so gets no notifications


class NotificationListView(RoleRequiredMixin, ListView):
    allowed_roles = NOTIFIED_ROLES
    template_name = "notifications/notification_list.html"
    context_object_name = "notifications"
    paginate_by = 20

    def get_queryset(self):
        services.sweep_overdue_if_due()
        if self.request.user.is_manager_role:
            services.sweep_unassigned_if_due(self.request.user)
        qs = Notification.objects.filter(recipient=self.request.user).select_related("task")
        if self.request.GET.get("filter") == "unread":
            qs = qs.filter(is_read=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["querystring"] = params.urlencode()
        ctx["only_unread"] = self.request.GET.get("filter") == "unread"
        return ctx


class NotificationOpenView(RoleRequiredMixin, View):
    """Marks one notification read and goes to the task it is about."""
    allowed_roles = NOTIFIED_ROLES

    def get(self, request, pk):
        note = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if not note.is_read:
            note.is_read, note.read_at = True, timezone.now()
            note.save(update_fields=["is_read", "read_at"])
        return redirect(note.url)


class NotificationReadAllView(RoleRequiredMixin, View):
    allowed_roles = NOTIFIED_ROLES

    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True, read_at=timezone.now())
        return redirect("notification_list")
