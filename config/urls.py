from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("projects/", include("projects.urls")),
    path("tasks/", include("tasks.urls")),
    path("analytics/", include("analytics.urls")),
    path("notifications/", include("notifications.urls")),
    path("", include("accounts.urls")),
]
