from django.contrib import admin

from .models import ConfigurationIA, DecisionModeration, Signalement


@admin.register(Signalement)
class SignalementAdmin(admin.ModelAdmin):
    list_display = ("publication", "reporter", "reason", "status", "handled_by", "created_at")
    list_filter = ("status", "reason")
    search_fields = ("publication__text", "reporter__username", "details")
    raw_id_fields = ("publication", "reporter", "handled_by")


@admin.register(ConfigurationIA)
class ConfigurationIAAdmin(admin.ModelAdmin):
    list_display = ("threshold_review", "threshold_block", "max_retries", "updated_at", "updated_by")

    def has_add_permission(self, request):
        return not ConfigurationIA.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DecisionModeration)
class DecisionModerationAdmin(admin.ModelAdmin):
    list_display = ("publication", "action", "moderator", "previous_status", "waited_seconds", "created_at")
    list_filter = ("action",)
    raw_id_fields = ("publication", "moderator")
