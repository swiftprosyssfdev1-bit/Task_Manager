from django.core.management.base import BaseCommand

from notifications.services import notify_overdue


class Command(BaseCommand):
    help = "Create overdue-task notifications (once per task and deadline). Schedule it daily, e.g. with cron."

    def handle(self, *args, **options):
        self.stdout.write(f"{notify_overdue()} overdue notification(s) created.")
