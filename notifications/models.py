from django.conf import settings
from django.db import models
from django.urls import reverse


class Notification(models.Model):
    class Kind(models.TextChoices):
        ASSIGNED = "assigned", "Task assigned"
        REASSIGNED = "reassigned", "Task reassigned"
        SUBMITTED = "submitted", "Submitted for review"
        APPROVED = "approved", "Approved"
        REWORK = "rework", "Sent back for rework"
        BLOCKED = "blocked", "Blocked"
        STATUS = "status", "Status changed"
        DEADLINE = "deadline", "Deadline changed"
        COMMENT = "comment", "New comment"
        OVERDUE = "overdue", "Overdue"

    # Bootstrap icon and colour for each kind, used by the list page.
    STYLE = {
        Kind.ASSIGNED: ("bi-plus-circle", "primary"), Kind.REASSIGNED: ("bi-arrow-left-right", "secondary"),
        Kind.SUBMITTED: ("bi-hourglass-split", "info"), Kind.APPROVED: ("bi-check-circle", "success"),
        Kind.REWORK: ("bi-arrow-counterclockwise", "warning"), Kind.BLOCKED: ("bi-slash-circle", "danger"),
        Kind.STATUS: ("bi-arrow-repeat", "secondary"), Kind.DEADLINE: ("bi-calendar-event", "warning"),
        Kind.COMMENT: ("bi-chat-left-text", "primary"), Kind.OVERDUE: ("bi-exclamation-triangle", "danger"),
    }

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    message = models.CharField(max_length=300)
    task = models.ForeignKey("tasks.Task", null=True, blank=True, on_delete=models.CASCADE,
                             related_name="notifications")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # Set only for events that must fire once (e.g. "task X is overdue for deadline D"); NULLs never collide.
    dedupe_key = models.CharField(max_length=80, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "is_read", "created_at"])]
        constraints = [models.UniqueConstraint(fields=["recipient", "dedupe_key"], name="notification_once_per_key")]

    def __str__(self):
        return f"{self.recipient_id}: {self.message}"

    @property
    def icon(self):
        return self.STYLE[self.kind][0]

    @property
    def color(self):
        return self.STYLE[self.kind][1]

    @property
    def url(self):
        return reverse("task_detail", args=[self.task_id]) if self.task_id else reverse("notification_list")
