from django.urls import path

from . import views

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="notification_list"),
    path("read-all/", views.NotificationReadAllView.as_view(), name="notification_read_all"),
    path("<int:pk>/open/", views.NotificationOpenView.as_view(), name="notification_open"),
]
