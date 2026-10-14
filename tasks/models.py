from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .attachments import AttachmentBase


def sum_hours(path="duration_hours"):
    return Coalesce(Sum(path), Value(Decimal("0.00")),
                    output_field=models.DecimalField(max_digits=8, decimal_places=2))


class Technology(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "technologies"

    def __str__(self):
        return self.name


class TaskQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Single source of truth for who may see which tasks."""
        if user.is_admin_role:
            return self.all()
        if user.is_manager_role:
            scope = Q(project__manager=user) | Q(assigned_by=user)
            if user.department_id:
                scope |= Q(project__department_id=user.department_id)
            return self.filter(scope)
        return self.filter(assigned_to=user)

    def overdue(self):
        return self.filter(est_date__lt=timezone.localdate(), status__in=Task.OPEN_STATUSES)

    def with_hours(self):
        """Adds logged_hours (sum of work logs) and last_work_date."""
        return self.annotate(logged_hours=sum_hours("work_logs__duration_hours"),
                             last_work_date=Max("work_logs__work_date"))


class Task(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        IN_PROGRESS = "in_progress", "In Progress"
        ON_HOLD = "on_hold", "On Hold"
        BLOCKED = "blocked", "Blocked"
        PENDING_REVIEW = "pending_review", "Pending Review"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    OPEN_STATUSES = (Status.NOT_STARTED, Status.IN_PROGRESS, Status.ON_HOLD, Status.BLOCKED, Status.PENDING_REVIEW)
    TERMINAL_STATUSES = (Status.COMPLETED, Status.CANCELLED)
    STATUS_COLORS = {
        Status.NOT_STARTED: "secondary", Status.IN_PROGRESS: "primary", Status.ON_HOLD: "warning",
        Status.BLOCKED: "danger", Status.PENDING_REVIEW: "info", Status.COMPLETED: "success",
        Status.CANCELLED: "dark",
    }
    PRIORITY_COLORS = {Priority.LOW: "secondary", Priority.MEDIUM: "primary",
                       Priority.HIGH: "warning", Priority.CRITICAL: "danger"}

    title = models.CharField(max_length=200)
    description = models.TextField()
    project = models.ForeignKey("projects.Project", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="tasks")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                    related_name="tasks_assigned")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    assign_date = models.DateField(default=timezone.localdate)
    est_date = models.DateField("Est. date", null=True, blank=True)
    end_date = models.DateField("End date", null=True, blank=True)
    completed_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    technologies = models.ManyToManyField(Technology, blank=True, related_name="tasks")
    backup = models.TextField(blank=True)
    remarks = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["assign_date"]),
            models.Index(fields=["est_date"]),
        ]
        constraints = [models.CheckConstraint(
            condition=Q(est_date__isnull=True) | Q(est_date__gte=F("assign_date")),
            name="task_est_on_or_after_assign")]

    def __str__(self):
        return f"{self.display_id} {self.title}"

    @property
    def display_id(self):
        return f"TSK-{self.pk:05d}" if self.pk else "New"

    @property
    def is_overdue(self):
        return bool(self.est_date and self.est_date < timezone.localdate() and self.status in self.OPEN_STATUSES)

    @property
    def days_taken(self):
        """Calendar days from assign date to completion (0 = finished the same day). None while open."""
        if self.completed_date and self.assign_date:
            return max(0, (self.completed_date - self.assign_date).days)
        return None

    @property
    def days_open(self):
        """Days an open task has been running, counting the assign day as day 1."""
        if self.status in self.OPEN_STATUSES and self.assign_date:
            return max(1, (timezone.localdate() - self.assign_date).days + 1)
        return None

    @property
    def duration_label(self):
        d = self.days_taken
        if d is not None:
            return "Same day" if d == 0 else f"{d} day{'s' if d != 1 else ''}"
        o = self.days_open
        return f"Day {o}" if o else "-"

    @property
    def status_color(self):
        return self.STATUS_COLORS.get(self.status, "secondary")

    @property
    def priority_color(self):
        return self.PRIORITY_COLORS.get(self.priority, "secondary")

    @property
    def total_hours(self):
        """Total hours is always summed from work logs, never stored."""
        return self.work_logs.aggregate(total=sum_hours())["total"]

    def can_log_work(self, user):
        return user.pk == self.assigned_to_id and not self.is_archived and self.status != self.Status.CANCELLED

    def clean(self):
        if self.est_date and self.assign_date and self.est_date < self.assign_date:
            raise ValidationError({"est_date": "Estimated date cannot be before the assign date."})
        if self.end_date and self.assign_date and self.end_date < self.assign_date:
            raise ValidationError({"end_date": "End date cannot be before the assign date."})

    def save(self, *args, **kwargs):
        # Completion is stamped automatically and cleared if the task is reopened.
        if self.status == self.Status.COMPLETED:
            self.completed_date = self.completed_date or timezone.localdate()
            self.completed_at = self.completed_at or timezone.now()
        else:
            self.completed_date = None
            self.completed_at = None
        super().save(*args, **kwargs)


class TaskAssignmentHistory(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignment_history")
    from_employee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    to_employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name_plural = "task assignment history"


class TaskStatusHistory(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=20, blank=True)  # blank = task creation
    to_status = models.CharField(max_length=20, choices=Task.Status.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]
        indexes = [models.Index(fields=["task", "changed_at"])]
        verbose_name_plural = "task status history"

    @property
    def from_label(self):
        return Task.Status(self.from_status).label if self.from_status else "New"

    @property
    def to_label(self):
        return Task.Status(self.to_status).label


class TaskDeadlineChange(models.Model):
    """Estimated-date changes, kept so analytics can judge delays against the approved deadline."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="deadline_changes")
    old_date = models.DateField(null=True, blank=True)
    new_date = models.DateField(null=True, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]


class TaskComment(models.Model):
    class Kind(models.TextChoices):
        COMMENT = "comment", "Comment"
        FEEDBACK = "feedback", "Review feedback"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.COMMENT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class TaskWorkLog(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="work_logs")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_logs")
    work_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    duration_hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("24"))])
    description = models.TextField("Work performed")
    technologies = models.ManyToManyField(Technology, blank=True, related_name="work_logs")
    remarks = models.TextField(blank=True)
    attachment = models.FileField(upload_to="worklogs/%Y/%m/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "-created_at"]
        indexes = [models.Index(fields=["task", "work_date"]), models.Index(fields=["employee", "work_date"])]
        constraints = [models.CheckConstraint(
            condition=Q(duration_hours__gt=0) & Q(duration_hours__lte=24), name="worklog_duration_valid")]

    def __str__(self):
        return f"{self.task.display_id} {self.work_date} {self.duration_hours}h"


class TaskWorkLogAudit(models.Model):
    """One row per field a manager corrects on a work log."""
    worklog = models.ForeignKey(TaskWorkLog, on_delete=models.CASCADE, related_name="audits")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)
    field = models.CharField(max_length=50)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    reason = models.TextField()

    class Meta:
        ordering = ["-changed_at", "-id"]


class TaskAttachment(AttachmentBase):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")


class TaskCommentAttachment(AttachmentBase):
    comment = models.ForeignKey(TaskComment, on_delete=models.CASCADE, related_name="attachments")
