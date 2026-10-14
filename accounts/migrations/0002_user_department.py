import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("projects", "0003_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="department",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="staff", to="projects.department"),
        ),
    ]
