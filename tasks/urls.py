from django.urls import path

from . import views

urlpatterns = [
    path("", views.TaskListView.as_view(), name="task_list"),
    path("new/", views.TaskCreateView.as_view(), name="task_create"),
    path("<int:pk>/", views.TaskDetailView.as_view(), name="task_detail"),
    path("<int:pk>/edit/", views.TaskUpdateView.as_view(), name="task_update"),
    path("<int:pk>/archive/", views.TaskArchiveView.as_view(), name="task_archive"),
    path("<int:pk>/comment/", views.TaskCommentView.as_view(), name="task_comment"),
    path("<int:pk>/review/", views.TaskReviewView.as_view(), name="task_review"),
    path("<int:pk>/log-work/", views.WorkLogCreateView.as_view(), name="worklog_create"),
    path("worklogs/<int:pk>/edit/", views.WorkLogUpdateView.as_view(), name="worklog_update"),
    path("worklogs/<int:pk>/attachment/", views.WorkLogAttachmentView.as_view(), name="worklog_attachment"),
    path("attachments/<str:kind>/<int:pk>/", views.AttachmentDownloadView.as_view(), name="attachment_download"),
    path("attachments/<str:kind>/<int:pk>/delete/", views.AttachmentDeleteView.as_view(), name="attachment_delete"),
    path("technologies/", views.TechnologyListView.as_view(), name="technology_list"),
    path("technologies/new/", views.TechnologyCreateView.as_view(), name="technology_create"),
    path("technologies/<int:pk>/edit/", views.TechnologyUpdateView.as_view(), name="technology_update"),
]
