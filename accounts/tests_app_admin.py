from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from projects.models import Department

from .models import User


class AppAdminTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Software")
        self.super = User.objects.create_superuser("root", "r@x.com", "pw")
        self.app_admin = User.objects.create_user("appadmin", password="pw", role=User.Role.ADMIN)

    def test_app_admin_is_never_staff_even_if_flag_is_set(self):
        self.app_admin.is_staff = True
        self.app_admin.save()
        self.app_admin.refresh_from_db()
        self.assertFalse(self.app_admin.is_staff)

    def test_superuser_keeps_staff_and_admin_role(self):
        self.super.refresh_from_db()
        self.assertTrue(self.super.is_staff)
        self.assertEqual(self.super.role, User.Role.ADMIN)

    def test_app_admin_cannot_open_django_admin(self):
        self.client.force_login(self.app_admin)
        resp = self.client.get("/admin/")
        self.assertRedirects(resp, reverse("dashboard"), fetch_redirect_response=False)

    def test_superuser_can_open_django_admin(self):
        self.client.force_login(self.super)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_app_admin_can_use_the_app(self):
        self.client.force_login(self.app_admin)
        for name in ("dashboard", "manager_list", "user_list", "department_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_app_has_no_page_to_add_more_admins(self):
        self.client.force_login(self.app_admin)
        self.assertEqual(self.client.get("/admins/").status_code, 404)
        self.assertEqual(self.client.get("/admins/new/").status_code, 404)


class CreateProjectSuperAdminCommandTests(TestCase):
    def test_creates_one_non_staff_admin(self):
        call_command("create_project_super_admin", "sps_admin", "--password", "pw123")
        user = User.objects.get(username="sps_admin")
        self.assertEqual((user.role, user.is_staff, user.is_superuser), (User.Role.ADMIN, False, False))
        self.assertTrue(user.check_password("pw123"))

    def test_second_admin_is_refused(self):
        call_command("create_project_super_admin", "first", "--password", "pw123")
        with self.assertRaises(CommandError):
            call_command("create_project_super_admin", "second", "--password", "pw123")
        self.assertFalse(User.objects.filter(username="second").exists())

    def test_django_superuser_does_not_count_as_the_app_admin(self):
        User.objects.create_superuser("root", "r@x.com", "pw")
        call_command("create_project_super_admin", "sps_admin", "--password", "pw123")
        self.assertTrue(User.objects.filter(username="sps_admin").exists())
