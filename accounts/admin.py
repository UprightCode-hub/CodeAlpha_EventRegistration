from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Provides account management in Django admin."""

    list_display = (*DjangoUserAdmin.list_display, "role")
    list_filter = (*DjangoUserAdmin.list_filter, "role")
    search_fields = ("username", "email")
    fieldsets = (*DjangoUserAdmin.fieldsets, ("Account role", {"fields": ("role",)}))
    add_fieldsets = (
        *DjangoUserAdmin.add_fieldsets,
        ("Account role", {"fields": ("role",)}),
    )
