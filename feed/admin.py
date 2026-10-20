from django.contrib import admin

from .models import Recherche


@admin.register(Recherche)
class RechercheAdmin(admin.ModelAdmin):
    list_display = ("member", "query", "mode", "results_count", "created_at")
    list_filter = ("mode",)
    search_fields = ("query", "member__username")
