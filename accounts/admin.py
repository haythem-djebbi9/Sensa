from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Membre, Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False


@admin.register(Membre)
class MembreAdmin(UserAdmin):
    inlines = [ProfileInline]
    list_display = ("username", "email", "role", "email_confirmed", "is_active", "date_joined")
    list_filter = ("role", "is_active", "email_confirmed", "is_staff")
    search_fields = ("username", "email", "profile__display_name")
    ordering = ("-date_joined",)
    fieldsets = UserAdmin.fieldsets + (("Sensa", {"fields": ("role", "email_confirmed")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Sensa", {"fields": ("email", "role")}),)
