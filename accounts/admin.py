from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import LoginFailure, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Provides account management in Django admin."""

    list_display = (*DjangoUserAdmin.list_display, "role")  # type: ignore[misc]  # Django class attributes are generic.
    list_filter = (*DjangoUserAdmin.list_filter, "role")  # type: ignore[misc]  # Django class attributes are generic.
    search_fields = ("username", "email")
    fieldsets = (  # type: ignore[misc]  # Django class attribute typing.
        *DjangoUserAdmin.fieldsets,
        ("Account role", {"fields": ("role",)}),
    )
    add_fieldsets = (
        *DjangoUserAdmin.add_fieldsets,
        ("Account role", {"fields": ("role",)}),
    )


@admin.register(LoginFailure)
class LoginFailureAdmin(admin.ModelAdmin):
    """Shows sign-in failure records without allowing edits."""

    list_display = ("id", "username", "ip_address", "created_at")
    list_filter = ("created_at",)
    search_fields = ("username", "ip_address")

    def has_add_permission(self, request):
        """Prevent adding sign-in failures in the admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent editing sign-in failures in the admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deleting sign-in failures in the admin."""
        return False
