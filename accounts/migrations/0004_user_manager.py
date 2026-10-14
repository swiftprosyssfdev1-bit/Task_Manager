import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_backfill_departments"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="manager",
            field=models.ForeignKey(blank=True, help_text="The manager this employee reports to.", null=True,
                                    on_delete=django.db.models.deletion.SET_NULL, related_name="team",
                                    to=settings.AUTH_USER_MODEL),
        ),
    ]
