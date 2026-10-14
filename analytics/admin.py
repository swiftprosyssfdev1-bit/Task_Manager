from django.contrib import admin

from .models import Holiday


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name")
    date_hierarchy = "date"
    search_fields = ("name",)
