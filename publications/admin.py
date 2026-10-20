from django.contrib import admin

from .models import Audio, Image, Publication


class ImageInline(admin.TabularInline):
    model = Image
    extra = 0
    readonly_fields = ("description_ia",)


class AudioInline(admin.StackedInline):
    model = Audio
    extra = 0
    readonly_fields = ("transcription", "language", "segments")


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "author", "status", "visibility", "created_at")
    list_filter = ("status", "visibility")
    search_fields = ("text", "author__username")
    raw_id_fields = ("author",)
    inlines = [ImageInline, AudioInline]
