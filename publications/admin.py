from django.contrib import admin

from .models import Audio, Enregistrement, Image, Publication, Republication


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


@admin.register(Republication)
class RepublicationAdmin(admin.ModelAdmin):
    list_display = ("member", "publication", "created_at")
    search_fields = ("member__username", "publication__text")
    raw_id_fields = ("member", "publication")


@admin.register(Enregistrement)
class EnregistrementAdmin(admin.ModelAdmin):
    list_display = ("member", "publication", "created_at")
    raw_id_fields = ("member", "publication")
