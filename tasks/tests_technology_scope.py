"""Technologies are tracked only for departments that have "Track technologies" switched on (Software)."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from projects.models import Department, Project, ProjectMembership

from .models import Task, TaskWorkLog, Technology
from .tests import BaseCase


class TechnologyScopeCase(BaseCase):
    """BaseCase: Engineering tracks technologies (manager, emp1, emp2, project Alpha, tasks t1/t2).
    Here Sales does not (other_manager, sales_emp, project Pitch, task st)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sales_emp = User.objects.create_user("sales1", password="x", role=User.Role.EMPLOYEE,
                                                 department=cls.other_dept, manager=cls.other_manager)
        cls.pitch = Project.objects.create(name="Pitch", code="PIT", manager=cls.other_manager,
                                           department=cls.other_dept)
        ProjectMembership.objects.create(project=cls.pitch, user=cls.sales_emp)
        today = timezone.localdate()
        cls.st = Task.objects.create(title="Call clients", description="d", project=cls.pitch,
                                     assigned_to=cls.sales_emp, assigned_by=cls.other_manager,
                                     assign_date=today, est_date=today + timedelta(days=5))
        cls.python = Technology.objects.create(name="Python")

    def log_payload(self, **over):
        data = {"work_date": timezone.localdate().isoformat(), "duration_hours": "2", "description": "Worked",
                "remarks": ""}
        data.update(over)
        return data


class WhoTracksTechnologiesTests(TechnologyScopeCase):
    def test_flag_defaults_off_and_is_read_from_the_department(self):
        self.assertTrue(self.dept.uses_technologies)
        self.assertFalse(Department.objects.create(name="Support").uses_technologies)
        self.assertTrue(self.manager.tracks_technologies)
        self.assertTrue(self.emp1.tracks_technologies)
        self.assertFalse(self.other_manager.tracks_technologies)
        self.assertFalse(self.sales_emp.tracks_technologies)
        self.assertFalse(User.objects.create_user("nodept", password="x", role=User.Role.MANAGER).tracks_technologies)
        self.assertTrue(self.admin.tracks_technologies)  # the admin spans every department
        self.assertTrue(self.project.uses_technologies)
        self.assertFalse(self.pitch.uses_technologies)
        self.assertFalse(self.st.uses_technologies)

    def test_only_managers_of_such_departments_reach_the_technologies_pages(self):
        pages = [("technology_list", []), ("technology_create", []), ("technology_update", [self.python.pk])]
        self.client.force_login(self.manager)
        for name, args in pages:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        for user in (self.other_manager, self.admin, self.emp1, self.sales_emp):
            self.client.force_login(user)
            for name, args in pages:
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, (user.username, name))
        self.client.post(reverse("technology_create"), {"name": "Sneaky", "is_active": "on"})
        self.assertFalse(Technology.objects.filter(name="Sneaky").exists())

    def test_sidebar_link_only_for_technology_departments(self):
        link = reverse("technology_list")
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("dashboard")), link)
        for user in (self.other_manager, self.admin, self.sales_emp):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(reverse("dashboard")), link, msg_prefix=user.username)

    def test_admin_switches_a_department_on_and_off(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("department_update", args=[self.other_dept.pk]))
        self.assertIn("uses_technologies", r.context["form"].fields)
        self.client.post(reverse("department_update", args=[self.other_dept.pk]),
                         {"name": "Sales", "uses_technologies": "on", "is_active": "on"})
        self.other_dept.refresh_from_db()
        self.assertTrue(self.other_dept.uses_technologies)
        self.client.force_login(User.objects.get(pk=self.other_manager.pk))
        self.assertEqual(self.client.get(reverse("technology_list")).status_code, 200)

    def test_department_list_marks_technology_departments(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("department_list"))
        self.assertContains(r, 'title="Projects and tasks in this department record technologies"', count=1)

    def test_new_department_form_has_the_switch_off_by_default(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("department_create"), {"name": "Finance", "is_active": "on"})
        self.assertFalse(Department.objects.get(name="Finance").uses_technologies)


class FormsHideTechnologiesTests(TechnologyScopeCase):
    def test_project_form(self):
        self.client.force_login(self.manager)
        self.assertIn("technologies", self.client.get(reverse("project_create")).context["form"].fields)
        self.client.force_login(self.other_manager)
        self.assertNotIn("technologies", self.client.get(reverse("project_create")).context["form"].fields)
        self.assertNotIn("technologies", self.client.get(reverse("project_update", args=[self.pitch.pk])).context["form"].fields)

    def test_technologies_posted_for_a_non_technology_project_are_ignored(self):
        self.client.force_login(self.other_manager)
        r = self.client.post(reverse("project_create"), {
            "name": "Promo", "code": "PRO", "is_active": "on", "technologies": [self.python.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Project.objects.get(code="PRO").technologies.count(), 0)

    def test_task_update_form(self):
        self.client.force_login(self.emp1)
        self.assertIn("technologies", self.client.get(reverse("task_update", args=[self.t1.pk])).context["form"].fields)
        self.client.force_login(self.sales_emp)
        self.assertNotIn("technologies", self.client.get(reverse("task_update", args=[self.st.pk])).context["form"].fields)
        r = self.client.post(reverse("task_update", args=[self.st.pk]), {
            "status": "in_progress", "technologies": [self.python.pk], "remarks": "", "backup": ""})
        self.assertEqual(r.status_code, 302)
        self.st.refresh_from_db()
        self.assertEqual(self.st.status, "in_progress")  # the rest of the form still saves
        self.assertEqual(self.st.technologies.count(), 0)

    def test_work_log_form_and_propagation(self):
        self.client.force_login(self.emp1)
        self.assertIn("technologies", self.client.get(reverse("worklog_create", args=[self.t1.pk])).context["form"].fields)
        self.client.force_login(self.sales_emp)
        url = reverse("worklog_create", args=[self.st.pk])
        self.assertNotIn("technologies", self.client.get(url).context["form"].fields)
        r = self.client.post(url, self.log_payload(technologies=[self.python.pk]))
        self.assertEqual(r.status_code, 302)
        log = TaskWorkLog.objects.get(task=self.st)
        self.assertEqual((log.technologies.count(), self.st.technologies.count()), (0, 0))

    def test_work_log_correction_form(self):
        self.client.force_login(self.sales_emp)
        self.client.post(reverse("worklog_create", args=[self.st.pk]), self.log_payload())
        log = TaskWorkLog.objects.get(task=self.st)
        self.client.force_login(self.other_manager)
        form = self.client.get(reverse("worklog_update", args=[log.pk])).context["form"]
        self.assertNotIn("technologies", form.fields)


class PagesHideTechnologiesTests(TechnologyScopeCase):
    def setUp(self):
        self.t1.technologies.add(self.python)
        self.project.technologies.add(self.python)
        TaskWorkLog.objects.create(task=self.t1, employee=self.emp1, work_date=timezone.localdate(),
                                   duration_hours=1, description="x")

    def test_task_list_column(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("task_list")), "<th>Technologies</th>")
        for user in (self.other_manager, self.sales_emp):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(reverse("task_list")), "<th>Technologies</th>", msg_prefix=user.username)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("task_list")), "<th>Technologies</th>")

    def test_project_list_and_detail(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("project_list")), "<th>Technologies</th>")
        self.assertContains(self.client.get(reverse("project_detail", args=[self.project.pk])), "Technologies:")
        self.client.force_login(self.other_manager)
        self.assertNotContains(self.client.get(reverse("project_list")), "<th>Technologies</th>")
        self.assertNotContains(self.client.get(reverse("project_detail", args=[self.pitch.pk])), "Technologies:")

    def test_task_detail(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("task_detail", args=[self.t1.pk]))
        self.assertContains(r, "<dt class=\"col-5\">Technologies</dt>")
        self.assertContains(r, "<th>Technologies</th>")
        self.client.force_login(self.other_manager)
        r = self.client.get(reverse("task_detail", args=[self.st.pk]))
        self.assertNotContains(r, "<dt class=\"col-5\">Technologies</dt>")
        self.assertNotContains(r, "<th>Technologies</th>")

    def test_dashboard_project_card(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("dashboard")), "tech-chip")
        self.client.force_login(self.other_manager)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Pitch")
        self.assertNotContains(r, "No technologies set")

    def test_employee_report_card(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("employee_report", args=[self.emp1.pk])), "Technologies used")
        self.client.force_login(self.other_manager)
        self.assertNotContains(self.client.get(reverse("employee_report", args=[self.sales_emp.pk])), "Technologies used")
