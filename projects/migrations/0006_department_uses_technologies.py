from django.db import migrations, models


def enable_for_software(apps, schema_editor):
    """Only the Software department tracked technologies so far."""
    Department = apps.get_model("projects", "Department")
    Department.objects.filter(name__iexact="software").update(uses_technologies=True)


class Migration(migrations.Migration):

    dependencies = [("projects", "0005_projectattachment")]

    operations = [
        migrations.AddField(
            model_name="department",
            name="uses_technologies",
            field=models.BooleanField(
                default=False, verbose_name="Track technologies",
                help_text="Tick for departments whose projects and tasks record the technologies used (e.g. Software)."),
        ),
        migrations.RunPython(enable_for_software, migrations.RunPython.noop),
    ]
