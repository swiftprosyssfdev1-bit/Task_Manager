from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "department", "employee_code", "is_active")
    list_filter = ("role", "department", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Work profile", {"fields": ("role", "department", "employee_code", "designation")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Work profile", {"fields": ("role", "department", "employee_code", "designation")}),)
