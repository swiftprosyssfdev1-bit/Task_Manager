from django.urls import path

from . import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    # Admin: managers
    path("managers/", views.UserListView.as_view(), {"role": "manager"}, name="manager_list"),
    path("managers/new/", views.UserCreateView.as_view(), {"role": "manager"}, name="manager_create"),
    path("managers/<int:pk>/edit/", views.UserUpdateView.as_view(), {"role": "manager"}, name="manager_update"),
    path("managers/<int:pk>/toggle-active/", views.UserToggleActiveView.as_view(), {"role": "manager"},
         name="manager_toggle_active"),
    # Admin: employees (a manager sees only the list of their own employees)
    path("employees/", views.UserListView.as_view(), {"role": "employee"}, name="user_list"),
    path("employees/new/", views.UserCreateView.as_view(), {"role": "employee"}, name="user_create"),
    path("employees/<int:pk>/edit/", views.UserUpdateView.as_view(), {"role": "employee"}, name="user_update"),
    path("employees/<int:pk>/toggle-active/", views.UserToggleActiveView.as_view(), {"role": "employee"},
         name="user_toggle_active"),
]
