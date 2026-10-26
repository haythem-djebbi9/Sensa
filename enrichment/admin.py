from django.contrib import admin

from .models import (
    AnalyseIA, CacheIA, CorrectionTranscription, DigestIA, PipelineConfig, PromptIA, PublicationTag, Tag, TentativeIA,
    VocabulaireIA,
)


class TentativeInline(admin.TabularInline):
    model = TentativeIA
    extra = 0
    readonly_fields = ("number", "provider", "model", "success", "error", "tokens", "started_at", "duration_ms")
    can_delete = False


@admin.register(AnalyseIA)
class AnalyseIAAdmin(admin.ModelAdmin):
    list_display = ("publication", "type", "status", "attempts", "score", "provider", "model", "duration_ms", "cached", "updated_at")
    list_filter = ("type", "status", "provider", "cached")
    search_fields = ("publication__text", "target_id", "error")
    raw_id_fields = ("publication",)
    inlines = [TentativeInline]


@admin.register(CacheIA)
class CacheIAAdmin(admin.ModelAdmin):
    list_display = ("key", "type", "provider", "model", "hits", "created_at", "last_hit_at")
    list_filter = ("type",)


@admin.register(PipelineConfig)
class PipelineConfigAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PipelineConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class PublicationTagInline(admin.TabularInline):
    model = PublicationTag
    extra = 0
    raw_id_fields = ("publication",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    inlines = [PublicationTagInline]


@admin.register(VocabulaireIA)
class VocabulaireIAAdmin(admin.ModelAdmin):
    list_display = ("term", "category", "is_active", "variants", "created_by", "updated_at")
    list_filter = ("category", "is_active")
    search_fields = ("term", "variants")


@admin.register(PromptIA)
class PromptIAAdmin(admin.ModelAdmin):
    list_display = ("task", "version", "name", "is_active", "created_by", "updated_at")
    list_filter = ("task", "is_active")


@admin.register(CorrectionTranscription)
class CorrectionTranscriptionAdmin(admin.ModelAdmin):
    list_display = ("audio", "author", "is_revert", "note", "created_at")
    raw_id_fields = ("audio", "author")


@admin.register(DigestIA)
class DigestIAAdmin(admin.ModelAdmin):
    list_display = ("title", "period_start", "period_end", "status", "provider", "created_at")
    list_filter = ("status",)
