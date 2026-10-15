from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        EMPLOYEE = "employee", "Employee"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.EMPLOYEE, db_index=True)
    employee_code = models.CharField(max_length=30, unique=True, null=True, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    # Admin puts every manager and employee in a department, and each employee under one manager of it.
    department = models.ForeignKey("projects.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="staff")
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="team",
                                help_text="The manager this employee reports to.")

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
        elif self.role == self.Role.ADMIN:
            # An app admin works only inside this app: never staff, so no Django administration access.
            self.is_staff = False
        self.employee_code = self.employee_code or None
        super().save(*args, **kwargs)

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN

    @property
    def is_manager_role(self):
        return self.role == self.Role.MANAGER

    @property
    def is_employee_role(self):
        return self.role == self.Role.EMPLOYEE

    @property
    def can_manage_tasks(self):
        return self.role in (self.Role.ADMIN, self.Role.MANAGER)

    @property
    def tracks_technologies(self):
        """Whether this user's department records technologies. The admin spans departments, so always True."""
        if self.is_admin_role:
            return True
        return bool(self.department_id and self.department.uses_technologies)

    @property
    def can_assign_tasks(self):
        """Only managers create projects and assign tasks. Admin is view-only on those."""
        return self.role == self.Role.MANAGER

    def employees_in_scope(self):
        """Employees this user may see or assign: admin -> everyone, manager -> only the employees assigned to them."""
        employees = User.objects.filter(role=User.Role.EMPLOYEE)
        if self.is_admin_role:
            return employees
        if self.is_manager_role:
            return employees.filter(manager_id=self.pk)
        return employees.none()

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def __str__(self):
        return self.display_name
