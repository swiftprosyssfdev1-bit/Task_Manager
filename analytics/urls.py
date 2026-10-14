from django.urls import path

from . import views

urlpatterns = [
    path("", views.TeamAnalyticsView.as_view(), name="team_analytics"),
    path("employee/<int:pk>/", views.EmployeeReportView.as_view(), name="employee_report"),
]
