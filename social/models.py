"""Module 2 — Interactions sociales (Med Nour Romdhani) :
Abonnement, Commentaire, Reaction, Notification."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Abonnement(models.Model):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following", verbose_name="abonné"
    )
    followed = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="followers", verbose_name="suivi"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "abonnement"
        verbose_name_plural = "abonnements"
        constraints = [
            models.UniqueConstraint(fields=["follower", "followed"], name="abonnement_unique"),
            models.CheckConstraint(condition=~Q(follower=F("followed")), name="pas_d_auto_abonnement"),
        ]

    def __str__(self):
        return f"{self.follower} suit {self.followed}"


class Commentaire(models.Model):
    class Tone(models.TextChoices):
        POSITIVE = "positive", "Positif"
        NEUTRAL = "neutral", "Neutre"
        NEGATIVE = "negative", "Négatif"
        AGGRESSIVE = "aggressive", "Agressif"

    publication = models.ForeignKey("publications.Publication", on_delete=models.CASCADE, related_name="commentaires")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="commentaires")
    text = models.TextField("commentaire", max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    # Écrits par la tâche IA analyse_tone (module 2).
    tone = models.CharField("ton", max_length=10, choices=Tone.choices, blank=True)
    tone_score = models.FloatField("score du ton", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "commentaire"
        verbose_name_plural = "commentaires"

    def __str__(self):
        return f"{self.author} : {self.text[:40]}"

    def can_delete(self, user):
        return user.is_authenticated and (
            user.pk in (self.author_id, self.publication.author_id) or user.is_moderator
        )


class Reaction(models.Model):
    class Type(models.TextChoices):
        LIKE = "like", "J'aime"

    publication = models.ForeignKey("publications.Publication", on_delete=models.CASCADE, related_name="reactions")
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reactions")
    type = models.CharField(max_length=10, choices=Type.choices, default=Type.LIKE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "réaction"
        verbose_name_plural = "réactions"
        constraints = [models.UniqueConstraint(fields=["publication", "member", "type"], name="reaction_unique")]

    def __str__(self):
        return f"{self.member} {self.get_type_display()} {self.publication_id}"


class Notification(models.Model):
    class Type(models.TextChoices):
        LIKE = "like", "J'aime"
        COMMENT = "comment", "Commentaire"
        FOLLOW = "follow", "Nouvel abonné"
        REPUBLISH = "republish", "Republication"
        AI_READY = "ai_ready", "Publication enrichie"
        AI_FAILED = "ai_failed", "Échec de l'analyse IA"
        MODERATION = "moderation", "Modération"

    class Priority(models.IntegerChoices):
        LOW = 0, "Basse"
        NORMAL = 1, "Normale"
        HIGH = 2, "Haute"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    type = models.CharField(max_length=12, choices=Type.choices)
    publication = models.ForeignKey(
        "publications.Publication", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    commentaire = models.ForeignKey(Commentaire, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    message = models.CharField(max_length=255, blank=True)
    priority = models.PositiveSmallIntegerField(choices=Priority.choices, default=Priority.NORMAL)
    is_read = models.BooleanField("lue", default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "notification"
        verbose_name_plural = "notifications"

    def __str__(self):
        return f"{self.get_type_display()} → {self.recipient}"

    @property
    def title(self):
        actor = self.actor.display_name if self.actor else "Sensa"
        return {
            self.Type.LIKE: f"{actor} a aimé votre publication",
            self.Type.COMMENT: f"{actor} a commenté votre publication",
            self.Type.FOLLOW: f"{actor} vous suit désormais",
            self.Type.REPUBLISH: f"{actor} a republié votre publication",
            self.Type.AI_READY: "Votre publication est prête",
            self.Type.AI_FAILED: "L'analyse IA a échoué",
            self.Type.MODERATION: "Décision de modération",
        }.get(self.type, "Notification")

    @property
    def icon(self):
        return {
            self.Type.LIKE: ("heart", "coral"),
            self.Type.COMMENT: ("message", "violet"),
            self.Type.FOLLOW: ("user", "mint"),
            self.Type.REPUBLISH: ("repeat", "mint"),
            self.Type.AI_READY: ("spark", "ai"),
            self.Type.AI_FAILED: ("alert", "coral"),
            self.Type.MODERATION: ("shield", "violet"),
        }.get(self.type, ("bell", "violet"))
