from django.contrib import admin

from .models import Abonnement, Commentaire, Notification, Reaction


@admin.register(Abonnement)
class AbonnementAdmin(admin.ModelAdmin):
    list_display = ("follower", "followed", "created_at")
    search_fields = ("follower__username", "followed__username")
    raw_id_fields = ("follower", "followed")


@admin.register(Commentaire)
class CommentaireAdmin(admin.ModelAdmin):
    list_display = ("author", "publication", "short_text", "tone", "tone_score", "created_at")
    list_filter = ("tone",)
    search_fields = ("text", "author__username")
    raw_id_fields = ("publication", "author")

    @admin.display(description="commentaire")
    def short_text(self, obj):
        return obj.text[:60]


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ("member", "publication", "type", "created_at")
    raw_id_fields = ("publication", "member")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "type", "actor", "priority", "is_read", "created_at")
    list_filter = ("type", "is_read", "priority")
    raw_id_fields = ("recipient", "actor", "publication", "commentaire")
