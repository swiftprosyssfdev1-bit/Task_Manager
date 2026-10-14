from django.db import migrations


def backfill(apps, schema_editor):
    """Give tasks created in Phase 1 a starting status-history row."""
    Task = apps.get_model("tasks", "Task")
    History = apps.get_model("tasks", "TaskStatusHistory")
    rows = [History(task=t, from_status="", to_status=t.status, changed_by_id=t.assigned_by_id,
                    remarks="Imported from earlier version")
            for t in Task.objects.filter(status_history__isnull=True)]
    History.objects.bulk_create(rows)


class Migration(migrations.Migration):
    dependencies = [("tasks", "0002_phase2")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
