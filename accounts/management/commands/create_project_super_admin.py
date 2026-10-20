from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = ("Create THE app Admin (only one allowed): full access inside the app, but no Django administration "
            "(/admin/) access. Use createsuperuser for a Django administrator.")

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--email", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--password", help="Leave out to be asked for it (recommended).")

    def handle(self, *args, **options):
        existing = User.objects.filter(role=User.Role.ADMIN, is_superuser=False).first()
        if existing:
            raise CommandError(f"The app already has its admin ('{existing.username}'). Only one is allowed.")
        username = options["username"].strip()
        if not username:
            raise CommandError("Username is required.")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"User '{username}' already exists.")

        password = options["password"]
        if not password:
            password = getpass("Password: ")
            if password != getpass("Password (again): "):
                raise CommandError("Passwords did not match.")
        if not password:
            raise CommandError("Password cannot be empty.")

        user = User(username=username, email=options["email"], first_name=options["first_name"],
                    last_name=options["last_name"], role=User.Role.ADMIN, is_staff=False, is_superuser=False)
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"App admin '{username}' created (no Django admin access)."))
