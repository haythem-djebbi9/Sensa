"""Module 5 — Fil, recherche et recommandations (Taieb Bel Hadj Ali).

L'embedding est stocké sur Publication (champ `embedding`, module 1).
"""

from django.conf import settings
from django.db import models


class Recherche(models.Model):
    class Mode(models.TextChoices):
        KEYWORDS = "mots", "Mots-clés"
        SEMANTIC = "sens", "Par le sens"
        HASHTAG = "tag", "Hashtag"

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recherches")
    query = models.CharField("requête", max_length=200)
    mode = models.CharField(max_length=4, choices=Mode.choices, default=Mode.KEYWORDS)
    results_count = models.PositiveIntegerField("résultats", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "recherche"
        verbose_name_plural = "recherches"

    def __str__(self):
        return f"{self.member} : {self.query}"
