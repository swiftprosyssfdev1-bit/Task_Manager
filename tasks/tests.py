from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from projects.models import Department, Project, ProjectMembership

from .models import Task, TaskAssignmentHistory


class BaseCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        R = User.Role
        cls.dept = Department.objects.create(name="Engineering")
        cls.other_dept = Department.objects.create(name="Sales")
        cls.admin = User.objects.create_user("admin1", password="x", role=R.ADMIN)
        cls.manager = User.objects.create_user("mgr", password="x", role=R.MANAGER, department=cls.dept)
        cls.other_manager = User.objects.create_user("mgr2", password="x", role=R.MANAGER, department=cls.other_dept)
        cls.emp1 = User.objects.create_user("emp1", password="x", role=R.EMPLOYEE, department=cls.dept, manager=cls.manager)
        cls.emp2 = User.objects.create_user("emp2", password="x", role=R.EMPLOYEE, department=cls.dept, manager=cls.manager)
        cls.project = Project.objects.create(name="Alpha", code="ALP", manager=cls.manager, department=cls.dept)
        for e in (cls.emp1, cls.emp2):
            ProjectMembership.objects.create(project=cls.project, user=e)
        today = timezone.localdate()
        cls.t1 = Task.objects.create(title="T1", description="d", project=cls.project, assigned_to=cls.emp1,
                                     assigned_by=cls.manager, assign_date=today, est_date=today + timedelta(days=5))
        cls.t2 = Task.objects.create(title="T2", description="d", project=cls.project, assigned_to=cls.emp2,
                                     assigned_by=cls.manager, assign_date=today, est_date=today + timedelta(days=5))

    def create_payload(self, **over):
        data = self.payload(assignees=[self.emp1.pk])
        data.pop("assigned_to")
        data.update(over)
        return data

    def payload(self, **over):
        today = timezone.localdate()
        data = {"title": "New", "description": "desc", "project": self.project.pk, "assigned_to": self.emp1.pk,
                "priority": "medium", "status": "not_started", "assign_date": today.isoformat(),
                "est_date": (today + timedelta(days=3)).isoformat(), "remarks": ""}
        data.update(over)
        return data


class VisibilityTests(BaseCase):
    def test_employee_sees_only_own_tasks(self):
        self.client.force_login(self.emp1)
        r = self.client.get(reverse("task_list"))
        self.assertEqual([t.pk for t in r.context["tasks"]], [self.t1.pk])

    def test_employee_cannot_open_or_edit_others_task(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("task_detail", args=[self.t2.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("task_update", args=[self.t2.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("task_update", args=[self.t2.pk]), {"status": "in_progress"}).status_code, 404)

    def test_manager_sees_only_own_project_tasks(self):
        self.client.force_login(self.other_manager)
        self.assertEqual(self.client.get(reverse("task_detail", args=[self.t1.pk])).status_code, 404)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("task_detail", args=[self.t1.pk])).status_code, 200)

    def test_admin_sees_everything(self):
        self.client.force_login(self.admin)
        self.assertEqual(Task.objects.visible_to(self.admin).count(), 2)

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(reverse("task_list"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])


class PermissionTests(BaseCase):
    def test_employee_cannot_create_task(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("task_create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("task_create"), self.payload()).status_code, 403)

    def test_admin_cannot_create_project_or_task_or_edit_task(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("task_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("project_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("project_update", args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("task_update", args=[self.t1.pk])).status_code, 404)

    def test_admin_creates_managers_and_employees_manager_cannot(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("manager_create"), {
            "department": self.dept.pk, "username": "newmgr", "password1": "x", "password2": "x", "role": "admin"})
        self.assertEqual(r.status_code, 302)
        new = User.objects.get(username="newmgr")
        self.assertEqual((new.role, new.department, new.manager), (User.Role.MANAGER, self.dept, None))
        r = self.client.post(reverse("user_create"), {
            "department": self.dept.pk, "manager": self.manager.pk, "username": "newemp",
            "password1": "x", "password2": "x", "role": "manager"})
        self.assertEqual(r.status_code, 302)
        new = User.objects.get(username="newemp")
        self.assertEqual((new.role, new.department, new.manager), (User.Role.EMPLOYEE, self.dept, self.manager))
        # a manager account is not reachable through the employee pages, and vice versa
        self.assertEqual(self.client.get(reverse("user_update", args=[self.manager.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("manager_update", args=[self.emp1.pk])).status_code, 404)

    def test_manager_cannot_create_edit_or_deactivate_accounts(self):
        self.client.force_login(self.manager)
        for name, args in (("user_create", []), ("manager_create", []), ("manager_list", []),
                           ("user_update", [self.emp1.pk]), ("manager_update", [self.manager.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("user_toggle_active", args=[self.emp1.pk])).status_code, 403)
        self.client.post(reverse("user_create"), {"department": self.dept.pk, "manager": self.manager.pk,
                                                  "username": "sneaky", "password1": "x", "password2": "x"})
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_admin_sees_no_update_buttons_but_manager_and_employee_do(self):
        url = reverse("task_update", args=[self.t1.pk])
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("task_list")), url)
        self.assertNotContains(self.client.get(reverse("task_detail", args=[self.t1.pk])), url)
        for who in (self.manager, self.emp1):
            self.client.force_login(who)
            self.assertContains(self.client.get(reverse("task_list")), url)
            self.assertContains(self.client.get(reverse("task_detail", args=[self.t1.pk])), url)

    def test_manager_cannot_assign_to_non_member(self):
        outsider = User.objects.create_user("outsider", password="x", role=User.Role.EMPLOYEE)
        self.client.force_login(self.manager)
        r = self.client.post(reverse("task_create"), self.create_payload(assignees=[outsider.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.filter(title="New").exists())

    def test_manager_assigns_one_task_to_many_employees(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_create"), self.create_payload(assignees=[self.emp1.pk, self.emp2.pk]))
        self.assertEqual(Task.objects.filter(title="New").count(), 2)

    def test_manager_cannot_use_other_managers_project(self):
        self.client.force_login(self.other_manager)
        r = self.client.post(reverse("task_create"), self.create_payload())
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.filter(title="New").exists())

    def test_employee_cannot_archive(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.post(reverse("task_archive", args=[self.t1.pk])).status_code, 403)

    def test_users_and_projects_access(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("project_create")).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("project_create")).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 200)

    def test_employee_cannot_change_restricted_fields(self):
        self.client.force_login(self.emp1)
        original_est = self.t1.est_date
        self.client.post(reverse("task_update", args=[self.t1.pk]), {
            "status": "in_progress", "remarks": "started", "backup": "",
            "est_date": (original_est + timedelta(days=30)).isoformat(), "assigned_to": self.emp2.pk})
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "in_progress")
        self.assertEqual(self.t1.est_date, original_est)
        self.assertEqual(self.t1.assigned_to, self.emp1)

    @override_settings(REQUIRE_MANAGER_REVIEW=True)
    def test_employee_cannot_mark_completed_when_review_required(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]), {"status": "completed", "remarks": "", "backup": ""})
        self.t1.refresh_from_db()
        self.assertNotEqual(self.t1.status, "completed")
        self.client.post(reverse("task_update", args=[self.t1.pk]), {"status": "pending_review", "remarks": "", "backup": ""})
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "pending_review")

    @override_settings(REQUIRE_MANAGER_REVIEW=False)
    def test_employee_can_complete_when_review_not_required(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]), {"status": "completed", "remarks": "", "backup": ""})
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "completed")
        self.assertEqual(self.t1.completed_date, timezone.localdate())

    def test_employee_cannot_reopen_completed_task(self):
        self.t1.status = "completed"
        self.t1.save()
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]), {"status": "in_progress", "remarks": "", "backup": ""})
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "completed")


class AssignmentTests(BaseCase):
    def test_create_records_assigner_and_history(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse("task_create"), self.create_payload())
        self.assertEqual(r.status_code, 302)
        task = Task.objects.get(title="New")
        self.assertEqual(task.assigned_by, self.manager)
        self.assertEqual(task.assignment_history.count(), 1)
        self.assertIsNone(task.assignment_history.first().from_employee)

    def test_reassign_records_history(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_update", args=[self.t1.pk]), self.payload(title="T1", assigned_to=self.emp2.pk))
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.assigned_to, self.emp2)
        h = TaskAssignmentHistory.objects.get(task=self.t1)
        self.assertEqual((h.from_employee, h.to_employee, h.changed_by), (self.emp1, self.emp2, self.manager))

    def test_edit_without_reassign_adds_no_history(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_update", args=[self.t1.pk]), self.payload(title="Renamed"))
        self.assertEqual(TaskAssignmentHistory.objects.filter(task=self.t1).count(), 0)

    def test_assignee_must_be_project_member(self):
        outsider = User.objects.create_user("emp3", password="x", role=User.Role.EMPLOYEE)
        self.client.force_login(self.manager)
        r = self.client.post(reverse("task_create"), self.payload(assigned_to=outsider.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.filter(title="New").exists())


class ModelTests(BaseCase):
    def test_est_before_assign_is_invalid(self):
        t = Task(title="x", description="d", assigned_to=self.emp1,
                 assign_date=date(2026, 5, 10), est_date=date(2026, 5, 1))
        with self.assertRaises(ValidationError):
            t.full_clean()

    def test_task_without_estimate_is_valid_and_never_overdue(self):
        t = Task.objects.create(title="x", description="d", assigned_to=self.emp1, assign_date=date(2020, 1, 1))
        self.assertFalse(t.is_overdue)
        self.assertNotIn(t, Task.objects.overdue())

    def test_completion_is_stamped_and_cleared_on_reopen(self):
        self.t1.status = "completed"
        self.t1.save()
        self.assertEqual(self.t1.completed_date, timezone.localdate())
        self.assertIsNotNone(self.t1.completed_at)
        self.t1.status = "in_progress"
        self.t1.save()
        self.assertIsNone(self.t1.completed_date)

    def test_overdue_excludes_completed_tasks(self):
        past = timezone.localdate() - timedelta(days=10)
        late = Task.objects.create(title="late", description="d", assigned_to=self.emp1,
                                   assign_date=past - timedelta(days=5), est_date=past)
        done = Task.objects.create(title="done", description="d", assigned_to=self.emp1,
                                   assign_date=past - timedelta(days=5), est_date=past, status="completed")
        overdue = Task.objects.overdue()
        self.assertIn(late, overdue)
        self.assertNotIn(done, overdue)


class PageSmokeTests(BaseCase):
    def test_pages_render_for_each_role(self):
        pages = [reverse("dashboard"), reverse("task_list"), reverse("project_list"),
                 reverse("project_detail", args=[self.project.pk])]
        for user in (self.admin, self.manager, self.emp1):
            self.client.force_login(user)
            for url in pages:
                self.assertEqual(self.client.get(url).status_code, 200, f"{user.role} {url}")
        self.client.force_login(self.manager)
        for name in ("task_create", "project_create", "user_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("project_update", args=[self.project.pk])).status_code, 200)
        self.client.force_login(self.admin)
        for name in ("user_create", "manager_create", "manager_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_manager_creates_project_and_becomes_its_manager(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse("project_create"), {
            "department": self.dept.pk, "name": "Beta", "code": "BET", "is_active": "on", "team_members": [self.emp1.pk, self.emp2.pk]})
        self.assertEqual(r.status_code, 302)
        proj = Project.objects.get(code="BET")
        self.assertEqual(proj.manager, self.manager)
        self.assertEqual(proj.memberships.count(), 2)
