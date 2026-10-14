from django.conf import settings
from django.db import models
from django.db.models import Q


class ProjectQuerySet(models.QuerySet):
    def visible_to(self, user):
        if user.is_admin_role:
            return self.all()
        member_ids = ProjectMembership.objects.filter(user=user).values("project_id")
        if user.is_manager_role:
            return self.filter(self._manager_scope(user) | Q(pk__in=member_ids))
        return self.filter(pk__in=member_ids)

    @staticmethod
    def _manager_scope(user):
        """A manager owns their own projects plus every project in their department."""
        scope = Q(manager=user)
        if user.department_id:  # never match "department IS NULL" for a manager without a department
            scope |= Q(department_id=user.department_id)
        return scope

    def managed_by(self, user):
        """Projects a manager may edit and assign tasks in."""
        return self.filter(self._manager_scope(user))


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Project(models.Model):
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name="projects")
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="managed_projects")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="ProjectMembership",
                                     related_name="projects")
    technologies = models.ManyToManyField("tasks.Technology", blank=True, related_name="projects")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    est_date = models.DateField("Est. date", null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(
            condition=Q(end_date__isnull=True) | Q(start_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
            name="project_end_after_start")]

    def __str__(self):
        return self.name


class ProjectMembership(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships")
    joined_on = models.DateField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="unique_project_member")]

    def __str__(self):
        return f"{self.user} in {self.project}"
