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
        self.email = self.email.lower()
        super().save(*args, **kwargs)
