from django.contrib import messages
from django.shortcuts import redirect


class DjangoAdminSiteGuardMiddleware:
    """Only superusers may open /admin/ (Django administration). Any other signed-in user, including the
    app's Admin role, is sent back to the app dashboard."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            user = request.user
            if user.is_authenticated and not user.is_superuser:
                messages.error(request, "You do not have access to the Django administration site.")
                return redirect("dashboard")
        return self.get_response(request)
