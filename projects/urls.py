from django.urls import path

from . import views

urlpatterns = [
    path("", views.ProjectListView.as_view(), name="project_list"),
    path("new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("departments/", views.DepartmentListView.as_view(), name="department_list"),
    path("departments/new/", views.DepartmentCreateView.as_view(), name="department_create"),
    path("departments/<int:pk>/edit/", views.DepartmentUpdateView.as_view(), name="department_update"),
    path("<int:pk>/", views.ProjectDetailView.as_view(), name="project_detail"),
    path("<int:pk>/edit/", views.ProjectUpdateView.as_view(), name="project_update"),
]
