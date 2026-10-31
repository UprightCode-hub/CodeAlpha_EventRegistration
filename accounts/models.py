from django.contrib.auth.models import AbstractUser
from django.db import models

from accounts.managers import UserManager


class User(AbstractUser):
    """An account that can organize events or attend them."""

    class Role(models.TextChoices):
        """Lists the account roles available to users."""

        ORGANIZER = "organizer", "Organizer"
        ATTENDEE = "attendee", "Attendee"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ATTENDEE)

    objects = UserManager()

    REQUIRED_FIELDS = ["email"]

    def save(self, *args, **kwargs):
        """Store the account email in lowercase."""
        self.email = self.email.lower()
        super().save(*args, **kwargs)

class LoginFailure(models.Model):
    """Stores a failed sign-in for a username and network address."""

    username = models.CharField(max_length=150)
    ip_address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Indexes recent failures by username and address."""

        indexes = (
            models.Index(
                fields=("username", "ip_address", "created_at"),
                name="login_failure_lookup_idx",
            ),
        )
