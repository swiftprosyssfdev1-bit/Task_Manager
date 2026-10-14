from django.db import migrations


def backfill(apps, schema_editor):
    """Employees created before managers were linked: report to the first manager of their department."""
    User = apps.get_model("accounts", "User")
    for emp in User.objects.filter(role="employee", manager__isnull=True, department__isnull=False):
        boss = User.objects.filter(role="manager", department_id=emp.department_id).order_by("id").first()
        if boss:
            User.objects.filter(pk=emp.pk).update(manager_id=boss.pk)


class Migration(migrations.Migration):

    dependencies = [("accounts", "0004_user_manager")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
