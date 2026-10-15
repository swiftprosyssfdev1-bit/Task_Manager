from django.test import TestCase
from django.urls import reverse

from projects.models import Department

from .forms import UserCreateForm, UserUpdateForm
from .models import User


class AppAdminTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Software")
        self.super = User.objects.create_superuser("root", "r@x.com", "pw")
        self.app_admin = User.objects.create_user("appadmin", password="pw", role=User.Role.ADMIN)
        self.manager = User.objects.create_user("boss", password="pw", role=User.Role.MANAGER, department=self.dept)

    def form_data(self, username="newadmin"):
        return {"username": username, "first_name": "New", "last_name": "Admin", "email": "n@x.com",
                "employee_code": "", "designation": "", "password1": "abc12345", "password2": "abc12345"}

    def test_app_admin_is_never_staff_even_if_flag_is_set(self):
        self.app_admin.is_staff = True
        self.app_admin.save()
        self.app_admin.refresh_from_db()
        self.assertFalse(self.app_admin.is_staff)

    def test_superuser_keeps_staff_and_admin_role(self):
        self.super.refresh_from_db()
        self.assertTrue(self.super.is_staff)
        self.assertEqual(self.super.role, User.Role.ADMIN)

    def test_form_creates_non_staff_admin_without_department(self):
        form = UserCreateForm(self.form_data(), role=User.Role.ADMIN)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIsNone(user.department)

    def test_app_admin_cannot_open_django_admin(self):
        self.client.force_login(self.app_admin)
        resp = self.client.get("/admin/")
        self.assertRedirects(resp, reverse("dashboard"), fetch_redirect_response=False)

    def test_superuser_can_open_django_admin(self):
        self.client.force_login(self.super)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_app_admin_can_use_the_app(self):
        self.client.force_login(self.app_admin)
        for name in ("dashboard", "admin_list", "manager_list", "user_list", "department_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_app_admin_can_create_another_admin_through_the_page(self):
        self.client.force_login(self.app_admin)
        resp = self.client.post(reverse("admin_create"), self.form_data("second"))
        self.assertRedirects(resp, reverse("admin_list"))
        second = User.objects.get(username="second")
        self.assertEqual((second.role, second.is_staff, second.is_superuser), (User.Role.ADMIN, False, False))

    def test_new_admin_page_works_with_no_departments(self):
        Department.objects.update(is_active=False)
        self.client.force_login(self.app_admin)
        self.assertEqual(self.client.get(reverse("admin_create")).status_code, 200)

    def test_superuser_hidden_from_app_admin_list(self):
        self.client.force_login(self.app_admin)
        resp = self.client.get(reverse("admin_list"))
        names = [u.username for u in resp.context["users"]]
        self.assertIn("appadmin", names)
        self.assertNotIn("root", names)

    def test_edit_and_toggle_admin(self):
        other = User.objects.create_user("other", password="pw", role=User.Role.ADMIN)
        self.client.force_login(self.app_admin)
        resp = self.client.post(reverse("admin_update", args=[other.pk]),
                                {"first_name": "O", "last_name": "T", "email": "o@x.com", "employee_code": "",
                                 "designation": "", "is_active": "on"})
        self.assertRedirects(resp, reverse("admin_list"))
        self.client.post(reverse("admin_toggle_active", args=[other.pk]))
        other.refresh_from_db()
        self.assertFalse(other.is_active)

    def test_cannot_edit_superuser_from_app(self):
        self.client.force_login(self.app_admin)
        self.assertEqual(self.client.get(reverse("admin_update", args=[self.super.pk])).status_code, 404)

    def test_manager_cannot_reach_admin_pages(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("admin_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin_create")).status_code, 403)
