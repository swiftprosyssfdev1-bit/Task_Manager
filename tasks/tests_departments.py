"""Department scoping: the admin owns departments and managers; a manager owns one department's people and work."""
from django.urls import reverse

from accounts.models import User
from analytics.forms import AnalyticsFilterForm
from projects.models import Department, Project, ProjectMembership

from .models import Task
from .tests import BaseCase


class AdminOwnsDepartmentsTests(BaseCase):
    def test_only_admin_can_see_and_change_departments(self):
        for u in (self.emp1, self.manager):
            self.client.force_login(u)
            for name in ("department_list", "department_create"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, (u.username, name))
            self.assertEqual(self.client.get(reverse("department_update", args=[self.dept.pk])).status_code, 403)
        self.client.post(reverse("department_create"), {"name": "Finance", "is_active": "on"})
        self.assertFalse(Department.objects.filter(name="Finance").exists())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("department_list")).status_code, 200)
        self.client.post(reverse("department_create"), {"name": "Finance", "is_active": "on"})
        self.assertTrue(Department.objects.filter(name="Finance").exists())

    def test_departments_link_only_shown_to_admin(self):
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("department_list"))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard")), reverse("department_list"))

    def test_department_list_shows_its_manager(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("department_list"))
        row = next(d for d in r.context["departments"] if d.pk == self.dept.pk)
        self.assertEqual(row.managers, [self.manager])
        self.assertEqual(row.employee_count, 2)

    def test_admin_adds_manager_under_a_department(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("manager_create"), {
            "department": self.other_dept.pk, "username": "newmgr", "password1": "x", "password2": "x"})
        self.assertEqual(r.status_code, 302)
        m = User.objects.get(username="newmgr")
        self.assertEqual((m.role, m.department), (User.Role.MANAGER, self.other_dept))

    def test_manager_requires_a_department(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("manager_create"), {"username": "nomgr", "password1": "x", "password2": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="nomgr").exists())

    def test_moving_manager_to_another_department_moves_their_team(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("manager_update", args=[self.manager.pk]), {
            "department": self.other_dept.pk, "first_name": "M", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        self.manager.refresh_from_db()
        self.emp1.refresh_from_db()
        self.assertEqual((self.manager.department, self.emp1.department), (self.other_dept, self.other_dept))

    def test_inactive_department_cannot_receive_a_manager(self):
        Department.objects.filter(pk=self.other_dept.pk).update(is_active=False)
        self.client.force_login(self.admin)
        r = self.client.post(reverse("manager_create"), {
            "department": self.other_dept.pk, "username": "x1", "password1": "x", "password2": "x"})
        self.assertEqual(r.status_code, 200)

    def test_department_row_links_to_add_manager(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("department_list"))
        self.assertContains(r, f'{reverse("manager_create")}?department={self.dept.pk}')


class AdminAssignsEmployeesTests(BaseCase):
    def test_edit_page_keeps_showing_the_signed_in_admin(self):
        self.client.force_login(self.admin)
        for name, target in (("user_update", self.emp1), ("manager_update", self.manager)):
            r = self.client.get(reverse(name, args=[target.pk]))
            self.assertEqual(r.context["user"], self.admin, name)
            self.assertEqual(r.context["account"], target, name)
            self.assertNotContains(r, "My tasks")
            self.assertContains(r, reverse("manager_list"))

    def payload(self, **over):
        data = {"department": self.dept.pk, "manager": self.manager.pk, "username": "newemp",
                "password1": "x", "password2": "x"}
        data.update(over)
        return data

    def test_admin_adds_employee_with_department_and_manager(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("user_create"), self.payload()).status_code, 302)
        e = User.objects.get(username="newemp")
        self.assertEqual((e.role, e.department, e.manager), (User.Role.EMPLOYEE, self.dept, self.manager))

    def test_employee_needs_both_department_and_manager(self):
        self.client.force_login(self.admin)
        for missing in ("department", "manager"):
            data = self.payload(username=f"no_{missing}")
            del data[missing]
            self.assertEqual(self.client.post(reverse("user_create"), data).status_code, 200, missing)
            self.assertFalse(User.objects.filter(username=f"no_{missing}").exists())

    def test_manager_must_belong_to_the_chosen_department(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("user_create"), self.payload(department=self.other_dept.pk))
        self.assertEqual(r.status_code, 200)
        self.assertIn("manager", r.context["form"].errors)
        self.assertFalse(User.objects.filter(username="newemp").exists())

    def test_admin_reassigns_employee_to_another_manager(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("user_update", args=[self.emp1.pk]), {
            "department": self.other_dept.pk, "manager": self.other_manager.pk, "first_name": "E", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        self.emp1.refresh_from_db()
        self.assertEqual((self.emp1.department, self.emp1.manager), (self.other_dept, self.other_manager))
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp1.pk])).status_code, 404)

    def test_add_manager_button_prefills_department_and_manager(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("user_create"), {"manager": self.manager.pk})
        self.assertEqual(r.context["form"].initial, {"manager": self.manager.pk, "department": self.dept.pk})

    def test_employee_page_redirects_to_manager_page_when_no_manager_exists(self):
        User.objects.filter(role=User.Role.MANAGER).update(is_active=False)
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("user_create")), reverse("manager_create"))

    def test_admin_sees_unassigned_employees_and_can_filter_by_department(self):
        lost = User.objects.create_user("lost", password="x", role=User.Role.EMPLOYEE)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("user_list"))
        self.assertIn(lost, r.context["users"])
        self.assertContains(r, "Not assigned")
        r = self.client.get(reverse("user_list"), {"department": self.dept.pk})
        self.assertEqual({u.pk for u in r.context["users"]}, {self.emp1.pk, self.emp2.pk})


class ManagerScopedToOwnTeamTests(BaseCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sales_emp = User.objects.create_user("sales1", password="x", role=User.Role.EMPLOYEE,
                                                 department=cls.other_dept, manager=cls.other_manager)
        # a colleague in the same department with an employee of their own
        cls.colleague = User.objects.create_user("mgr3", password="x", role=User.Role.MANAGER, department=cls.dept)
        cls.colleague_emp = User.objects.create_user("emp3", password="x", role=User.Role.EMPLOYEE,
                                                     department=cls.dept, manager=cls.colleague)

    def test_manager_lists_only_employees_assigned_to_them(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("user_list"))
        self.assertEqual({u.pk for u in r.context["users"]}, {self.emp1.pk, self.emp2.pk})

    def test_employee_list_is_read_only_for_manager(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("user_list"))
        self.assertNotContains(r, reverse("user_create"))
        self.assertNotContains(r, reverse("user_update", args=[self.emp1.pk]))
        self.assertContains(r, reverse("employee_report", args=[self.emp1.pk]))

    def test_manager_sidebar_has_no_managers_or_departments_page(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("dashboard"))
        self.assertNotContains(r, reverse("manager_list"))
        self.assertNotContains(r, reverse("department_list"))

    def test_manager_without_department_cannot_create_projects(self):
        User.objects.filter(pk=self.manager.pk).update(department=None)
        self.client.force_login(User.objects.get(pk=self.manager.pk))
        self.assertRedirects(self.client.get(reverse("project_create")), reverse("project_list"))

    def test_project_always_created_in_managers_department(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse("project_create"), {
            "department": self.other_dept.pk, "name": "Beta", "code": "BET", "is_active": "on",
            "team_members": [self.emp1.pk]})
        self.assertEqual(r.status_code, 302)
        p = Project.objects.get(code="BET")
        self.assertEqual((p.department, p.manager), (self.dept, self.manager))

    def test_project_form_offers_only_own_employees(self):
        self.client.force_login(self.manager)
        form = self.client.get(reverse("project_create")).context["form"]
        self.assertNotIn("department", form.fields)
        self.assertEqual(set(form.fields["team_members"].queryset), {self.emp1, self.emp2})
        self.assertNotIn(self.colleague_emp, form.fields["team_members"].queryset)

    def test_other_managers_employee_rejected_as_team_member(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse("project_create"), {
            "name": "Beta", "code": "BET", "is_active": "on", "team_members": [self.colleague_emp.pk]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Project.objects.filter(code="BET").exists())

    def test_other_department_employee_rejected_as_team_member(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse("project_create"), {
            "name": "Beta", "code": "BET", "is_active": "on", "team_members": [self.sales_emp.pk]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Project.objects.filter(code="BET").exists())

    def test_task_assignees_limited_to_own_employees(self):
        ProjectMembership.objects.create(project=self.project, user=self.sales_emp)  # legacy bad data
        self.client.force_login(self.manager)
        form = self.client.get(reverse("task_create")).context["form"]
        self.assertNotIn("department", form.fields)
        self.assertEqual(set(form.fields["assignees"].queryset), {self.emp1, self.emp2})

    def test_manager_sees_department_projects_but_not_other_departments(self):
        mine = Project.objects.create(name="Gamma", code="GAM", manager=self.colleague, department=self.dept)
        self.client.force_login(self.manager)
        visible = {p.pk for p in Project.objects.visible_to(self.manager)}
        self.assertEqual(visible, {self.project.pk, mine.pk})
        self.assertEqual(self.client.get(reverse("project_detail", args=[mine.pk])).status_code, 200)
        theirs = Project.objects.create(name="Pitch", code="PIT", manager=self.other_manager, department=self.other_dept)
        self.assertEqual(self.client.get(reverse("project_detail", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("project_update", args=[theirs.pk])).status_code, 404)

    def test_manager_without_department_does_not_see_projects_with_no_department(self):
        orphan = Project.objects.create(name="Orphan", code="ORP", manager=None, department=None)
        loner = User.objects.create_user("loner", password="x", role=User.Role.MANAGER)
        self.assertNotIn(orphan, Project.objects.visible_to(loner))
        self.assertEqual(Task.objects.visible_to(loner).count(), 0)

    def test_task_filter_and_analytics_filter_list_only_own_employees(self):
        self.client.force_login(self.manager)
        form = self.client.get(reverse("task_list")).context["filter_form"]
        self.assertEqual(set(form.fields["employee"].queryset), {self.emp1, self.emp2})
        form = AnalyticsFilterForm(user=self.manager)
        self.assertEqual(set(form.fields["employee"].queryset), {self.emp1, self.emp2})
        self.client.force_login(self.admin)
        form = AnalyticsFilterForm(user=self.admin)
        self.assertIn(self.sales_emp, form.fields["employee"].queryset)

    def test_employee_report_only_for_own_employees(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp1.pk])).status_code, 200)
        for other in (self.sales_emp, self.colleague_emp):
            self.assertEqual(self.client.get(reverse("employee_report", args=[other.pk])).status_code, 404)

    def test_admin_project_list_can_filter_by_department(self):
        other = Project.objects.create(name="Pitch", code="PIT", manager=self.other_manager, department=self.other_dept)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("project_list"), {"department": self.other_dept.pk})
        self.assertEqual([p.pk for p in r.context["projects"]], [other.pk])
