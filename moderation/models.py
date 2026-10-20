"""Module 4 — Modération et back-office (Chedly Rebai) : Signalement, ConfigurationIA."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ConfigurationIA(models.Model):
    """Réglages globaux de l'IA (singleton, modifiable par les administrateurs)."""

    threshold_review = models.FloatField("seuil de mise en revue", default=0.5)
    threshold_block = models.FloatField("seuil de blocage", default=0.85)
    max_retries = models.PositiveSmallIntegerField("tentatives maximum", default=3)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "configuration IA"
        verbose_name_plural = "configuration IA"

    def __str__(self):
        return f"Revue ≥ {self.threshold_review:.2f} · Blocage ≥ {self.threshold_block:.2f}"

    def clean(self):
        if not 0 <= self.threshold_review < self.threshold_block <= 1:
            raise ValidationError("Il faut 0 ≤ seuil de revue < seuil de blocage ≤ 1.")
        if not 1 <= self.max_retries <= 10:
            raise ValidationError("Le nombre de tentatives doit être compris entre 1 et 10.")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def decide(self, score):
        """< revue : READY · entre les deux : UNDER_REVIEW · ≥ blocage : BLOCKED."""
        if score >= self.threshold_block:
            return "BLOCK"
        if score >= self.threshold_review:
            return "REVIEW"
        return "ALLOW"


class Signalement(models.Model):
    class Reason(models.TextChoices):
        HARASSMENT = "HARASSMENT", "Harcèlement"
        HATE = "HATE", "Discours haineux"
        VIOLENCE = "VIOLENCE", "Violence ou menace"
        SEXUAL = "SEXUAL", "Contenu sexuel"
        MISLEADING = "MISLEADING", "Information trompeuse"
        SPAM = "SPAM", "Spam ou arnaque"
        OTHER = "OTHER", "Autre"

    class Status(models.TextChoices):
        OPEN = "OPEN", "En attente"
        RESOLVED = "RESOLVED", "Traité"
        DISMISSED = "DISMISSED", "Rejeté"

    publication = models.ForeignKey("publications.Publication", on_delete=models.CASCADE, related_name="signalements")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signalements_emis", verbose_name="signalé par"
    )
    reason = models.CharField("motif", max_length=12, choices=Reason.choices)
    details = models.TextField("précisions", max_length=500, blank=True)
    status = models.CharField("statut", max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    handled_by = models.ForeignKey(
        "accounts.Moderateur",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="signalements_traites",
        verbose_name="traité par",
    )
    handled_at = models.DateTimeField("traité le", null=True, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "signalement"
        verbose_name_plural = "signalements"
        constraints = [
            models.UniqueConstraint(
                fields=["publication", "reporter"],
                condition=models.Q(status="OPEN"),
                name="un_signalement_ouvert_par_membre",
            )
        ]

    def __str__(self):
        return f"{self.get_reason_display()} — {self.publication_id}"


class DecisionModeration(models.Model):
    """Journal des décisions humaines (audit et statistiques du back-office)."""

    class Action(models.TextChoices):
        APPROVE = "APPROVE", "Approuvée"
        BLOCK = "BLOCK", "Bloquée"
        DISMISS = "DISMISS", "Signalements rejetés"

    publication = models.ForeignKey("publications.Publication", on_delete=models.CASCADE, related_name="decisions")
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="decisions_moderation"
    )
    action = models.CharField(max_length=8, choices=Action.choices)
    previous_status = models.CharField(max_length=12)
    note = models.CharField("note", max_length=255, blank=True)
    waited_seconds = models.PositiveIntegerField("temps d'attente (s)", default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "décision de modération"
        verbose_name_plural = "décisions de modération"

    def __str__(self):
        return f"{self.get_action_display()} par {self.moderator} ({self.created_at:%d/%m %H:%M})"
