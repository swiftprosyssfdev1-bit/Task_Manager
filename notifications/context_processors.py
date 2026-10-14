from .models import Notification


def unread(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.is_admin_role:
        return {}
    return {"unread_notifications": Notification.objects.filter(recipient=user, is_read=False).count()}
