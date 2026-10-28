from django.core.management.base import BaseCommand

from notifications.services import notify_unassigned_employees


class Command(BaseCommand):
    help = "Notify managers about active employees who have not been assigned tasks today."

    def handle(self, *args, **options):
        count = notify_unassigned_employees()
        self.stdout.write(f"{count} unassigned employee reminder notification(s) created.")
