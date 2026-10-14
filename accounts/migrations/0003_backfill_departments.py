from collections import Counter

from django.db import migrations


def backfill(apps, schema_editor):
    """Existing accounts predate departments. Infer each one from the projects they already work on."""
    User = apps.get_model("accounts", "User")
    Project = apps.get_model("projects", "Project")
    Membership = apps.get_model("projects", "ProjectMembership")

    for manager in User.objects.filter(role="manager", department__isnull=True):
        dept = (Project.objects.filter(manager=manager, department__isnull=False)
                .values_list("department_id", flat=True).first())
        if dept:
            User.objects.filter(pk=manager.pk).update(department_id=dept)

    for emp in User.objects.filter(role="employee", department__isnull=True):
        depts = Membership.objects.filter(user=emp, project__department__isnull=False) \
            .values_list("project__department_id", flat=True)
        common = Counter(depts).most_common(1)
        if common:
            User.objects.filter(pk=emp.pk).update(department_id=common[0][0])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_department"),
        ("projects", "0004_project_est_date"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
