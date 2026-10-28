from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify_unassigned_employees, unassigned_employees_for_manager
from projects.forms import DepartmentForm, ProjectForm
from projects.models import Department, Project
from tasks.models import Task

User = get_user_model()


class NewFeaturesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_user", email="admin@example.com", password="password123"
        )
        self.dept = Department.objects.create(name="Software", branch="Chennai", uses_technologies=True)
        self.mgr1 = User.objects.create_user(
            username="mgr1", password="password123", role=User.Role.MANAGER,
            department=self.dept, branch="Chennai", first_name="Manager", last_name="One"
        )
        self.mgr2 = User.objects.create_user(
            username="mgr2", password="password123", role=User.Role.MANAGER,
            department=self.dept, branch="Madurai", first_name="Manager", last_name="Two"
        )
        self.emp1 = User.objects.create_user(
            username="emp1", password="password123", role=User.Role.EMPLOYEE,
            department=self.dept, manager=self.mgr1, branch="Chennai", first_name="Anitha", last_name="S"
        )
        self.emp2 = User.objects.create_user(
            username="emp2", password="password123", role=User.Role.EMPLOYEE,
            department=self.dept, manager=self.mgr1, branch="Tindivanam", first_name="Dharshini", last_name="K"
        )
        self.emp3 = User.objects.create_user(
            username="emp3", password="password123", role=User.Role.EMPLOYEE,
            department=self.dept, manager=self.mgr2, branch="Kanchipuram", first_name="Ravi", last_name="M"
        )

    def test_department_multiple_managers(self):
        """A department can have multiple managers added and listed."""
        managers = list(self.dept.staff.filter(role=User.Role.MANAGER))
        self.assertEqual(len(managers), 2)
        self.assertIn(self.mgr1, managers)
        self.assertIn(self.mgr2, managers)

        # Test DepartmentForm assigning managers
        mgr3 = User.objects.create_user(
            username="mgr3", password="password123", role=User.Role.MANAGER, first_name="Manager", last_name="Three"
        )
        form = DepartmentForm(
            data={"name": "Software", "branch": "Chennai", "managers": [self.mgr1.pk, mgr3.pk], "is_active": True},
            instance=self.dept
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved_dept = form.save()
        mgr3.refresh_from_db()
        self.mgr2.refresh_from_db()
        self.assertEqual(mgr3.department, saved_dept)
        self.assertIsNone(self.mgr2.department)  # deselected manager has department set to None

    def test_branches_choices_and_filtering(self):
        """Branches Chennai, Tindivanam, Kanchipuram, Madurai are supported."""
        expected_branches = {"Chennai", "Tindivanam", "Kanchipuram", "Madurai"}
        choices = {c[0] for c in User.Branch.choices}
        self.assertEqual(expected_branches, choices)

        self.client.force_login(self.admin)
        res = self.client.get(reverse("user_list") + "?branch=Chennai")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Anitha")
        self.assertNotContains(res, "Dharshini")

    def test_team_members_dropdown_in_project_form(self):
        """ProjectForm uses multi-select dropdown for team_members."""
        form = ProjectForm(user=self.mgr1)
        self.assertIn("multi-select-dropdown", form.fields["team_members"].widget.attrs.get("class", ""))
        # Manager 1 can see their team members
        self.assertIn(self.emp1, form.fields["team_members"].queryset)
        self.assertIn(self.emp2, form.fields["team_members"].queryset)
        self.assertNotIn(self.emp3, form.fields["team_members"].queryset)

    def test_unassigned_employee_notification_for_manager(self):
        """If manager forgets to assign task for an employee, manager is notified."""
        unassigned = unassigned_employees_for_manager(self.mgr1)
        self.assertEqual(len(unassigned), 2)
        self.assertIn(self.emp1, unassigned)
        self.assertIn(self.emp2, unassigned)

        # Trigger notification
        count = notify_unassigned_employees(self.mgr1)
        self.assertEqual(count, 2)

        notes = Notification.objects.filter(recipient=self.mgr1, kind=Notification.Kind.UNASSIGNED)
        self.assertEqual(notes.count(), 2)
        messages = [n.message for n in notes]
        self.assertTrue(any("Anitha" in m for m in messages))
        self.assertTrue(any("Dharshini" in m for m in messages))

        # Test deduplication: running it again today should not create duplicate notifications
        count_repeat = notify_unassigned_employees(self.mgr1)
        self.assertEqual(count_repeat, 0)

        # Now assign a task to Anitha
        proj = Project.objects.create(name="Project X", code="PX", department=self.dept, manager=self.mgr1)
        proj.members.add(self.emp1)
        Task.objects.create(
            title="Daily task for Anitha",
            description="Do work",
            project=proj,
            assigned_to=self.emp1,
            assigned_by=self.mgr1,
            assign_date=timezone.localdate(),
            status=Task.Status.IN_PROGRESS
        )

        unassigned_after = unassigned_employees_for_manager(self.mgr1)
        self.assertEqual(len(unassigned_after), 1)
        self.assertEqual(unassigned_after[0], self.emp2)

    def test_assignees_dropdown_in_task_create_form(self):
        """TaskCreateForm uses multi-select dropdown for assignees."""
        from tasks.forms import TaskCreateForm
        proj = Project.objects.create(name="Proj Y", code="PY", department=self.dept, manager=self.mgr1)
        proj.members.add(self.emp1, self.emp2)
        form = TaskCreateForm(user=self.mgr1)
        self.assertIn("multi-select-dropdown", form.fields["assignees"].widget.attrs.get("class", ""))

    def test_admin_dashboard_branches_and_create_manager(self):
        """Admin dashboard displays branch cards and allows creating managers through branches."""
        self.client.force_login(self.admin)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        branch_cards = res.context.get("branch_cards")
        self.assertIsNotNone(branch_cards)
        self.assertEqual(len(branch_cards), 4)
        names = {b["name"] for b in branch_cards}
        self.assertEqual(names, {"Chennai", "Tindivanam", "Kanchipuram", "Madurai"})

        # Check create manager URL with branch pre-fill
        res_create = self.client.get(reverse("manager_create") + "?branch=Madurai")
        self.assertEqual(res_create.status_code, 200)
        self.assertEqual(res_create.context["form"].initial.get("branch"), "Madurai")

