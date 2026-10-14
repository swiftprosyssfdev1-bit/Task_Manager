from django.contrib import admin

from . import models


@admin.register(models.Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "project", "assigned_to", "status", "priority", "est_date")
    list_filter = ("status", "priority", "project")
    search_fields = ("title", "description")


admin.site.register([models.Technology, models.TaskAssignmentHistory, models.TaskStatusHistory,
                     models.TaskDeadlineChange, models.TaskComment, models.TaskWorkLog, models.TaskWorkLogAudit])
