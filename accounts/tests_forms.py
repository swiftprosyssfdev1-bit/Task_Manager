from django.test import TestCase

from projects.models import Department

from .forms import UserCreateForm
from .models import User


class AdminPasswordTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Software")
        self.boss = User.objects.create_user("boss", password="x", role=User.Role.MANAGER, department=self.dept)

    def data(self, password):
        return {"department": self.dept.pk, "manager": self.boss.pk, "username": "sps001", "first_name": "A", "last_name": "B", "email": "a@b.com", "role": "employee",
                "employee_code": "sps001", "designation": "Dev", "password1": password, "password2": password}

    def test_admin_can_set_password_similar_to_username(self):
        form = UserCreateForm(self.data("sps001"))
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(user.check_password("sps001"))

    def test_short_numeric_password_is_accepted(self):
        self.assertTrue(UserCreateForm(self.data("1234")).is_valid())

    def test_mismatched_passwords_still_rejected(self):
        d = self.data("abc123")
        d["password2"] = "different"
        self.assertFalse(UserCreateForm(d).is_valid())

    def test_empty_password_still_rejected(self):
        self.assertFalse(UserCreateForm(self.data("")).is_valid())

    def test_duplicate_username_still_rejected(self):
        User.objects.create_user("sps001", password="x")
        self.assertFalse(UserCreateForm(self.data("whatever")).is_valid())
