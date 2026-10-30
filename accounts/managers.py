from django.contrib.auth.models import UserManager as DjangoUserManager


class UserManager(DjangoUserManager):
    """Creates users with normalized email addresses and organizer superusers."""

    def normalize_email(self, email):
        return super().normalize_email(email).lower()

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", "organizer")
        return super().create_superuser(username, email, password, **extra_fields)
