"""Module 3 — Enrichissement IA asynchrone (Haythem Djebbi).

- AnalyseIA      : une ligne par analyse (statut, résultat, score, tentatives, durée, modèle, jetons)
- TentativeIA    : journal de chaque tentative (fournisseur, modèle, latence, erreur)
- CacheIA        : une seule analyse par média (empreinte SHA-256 du fichier)
- PipelineConfig : étapes activables, modèle Whisper, langue, cache, chien de garde
- Tag / PublicationTag : tags avec leur source (auteur ou IA) et la confiance de l'IA
"""

from django.conf import settings
from django.db import models


class AnalyseIA(models.Model):
    """Une ligne par analyse IA : permet de suivre, relancer et auditer le pipeline."""

    class Type(models.TextChoices):
        DESCRIPTION = "description", "Description d'image"
        TRANSCRIPTION = "transcription", "Transcription audio"
        TAGS = "tags", "Tags et thème"
        SUMMARY = "summary", "Résumé IA"
        MODERATION = "moderation", "Modération (toxicité)"
        EMBEDDING = "embedding", "Embedding (recommandations)"
        TONE = "tone", "Ton d'un commentaire"
        TRANSLATION = "translation", "Traduction"

    class Status(models.TextChoices):
        PENDING = "PENDING", "En file"
        RUNNING = "RUNNING", "En cours"
        RETRY = "RETRY", "Relance programmée"
        DONE = "DONE", "Terminée"
        FAILED = "FAILED", "Échec"

    #: Analyses hors pipeline : elles n'influencent pas le statut de la publication.
    SIDE_TYPES = (Type.TONE, Type.TRANSLATION)

    publication = models.ForeignKey("publications.Publication", on_delete=models.CASCADE, related_name="analyses")
    type = models.CharField(max_length=16, choices=Type.choices)
    # Identifiant de l'objet analysé : image, audio, publication, commentaire (ou audio:langue).
    target_id = models.CharField("cible", max_length=64)
    result = models.JSONField("résultat", default=dict, blank=True)
    score = models.FloatField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField("tentatives", default=0)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    provider = models.CharField("fournisseur", max_length=40, blank=True)
    model = models.CharField("modèle", max_length=80, blank=True)
    tokens = models.PositiveIntegerField("jetons consommés", default=0)
    cached = models.BooleanField("résultat issu du cache", default=False)
    error = models.TextField("dernière erreur", blank=True)
    started_at = models.DateTimeField("démarrée le", null=True, blank=True)
    finished_at = models.DateTimeField("terminée le", null=True, blank=True)
    duration_ms = models.PositiveIntegerField("durée totale (ms)", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "analyse IA"
        verbose_name_plural = "analyses IA"
        constraints = [
            models.UniqueConstraint(fields=["publication", "type", "target_id"], name="unique_analyse_par_cible")
        ]

    def __str__(self):
        return f"{self.get_type_display()} · {self.get_status_display()} ({self.attempts} tentative(s))"

    @property
    def is_active(self):
        return self.status in {self.Status.PENDING, self.Status.RUNNING, self.Status.RETRY}

    @property
    def duration_label(self):
        if self.duration_ms is None:
            return "—"
        if self.duration_ms < 1000:
            return f"{self.duration_ms} ms"
        return f"{self.duration_ms / 1000:.1f} s".replace(".", ",")


class TentativeIA(models.Model):
    """Journal des tentatives : on sait pourquoi et après combien d'essais une analyse a réussi ou échoué."""

    analyse = models.ForeignKey(AnalyseIA, on_delete=models.CASCADE, related_name="tentatives")
    number = models.PositiveSmallIntegerField("tentative n°")
    provider = models.CharField(max_length=40, blank=True)
    model = models.CharField(max_length=80, blank=True)
    success = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    tokens = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["analyse_id", "started_at", "id"]
        verbose_name = "tentative IA"
        verbose_name_plural = "tentatives IA"

    def __str__(self):
        return f"Tentative {self.number} · {'succès' if self.success else 'échec'}"


class CacheIA(models.Model):
    """Résultat réutilisable : un même média n'est analysé qu'une fois (maîtrise des coûts)."""

    key = models.CharField("empreinte", max_length=64, unique=True)
    type = models.CharField(max_length=16, choices=AnalyseIA.Type.choices)
    result = models.JSONField(default=dict)
    provider = models.CharField(max_length=40, blank=True)
    model = models.CharField(max_length=80, blank=True)
    hits = models.PositiveIntegerField("réutilisations", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_hit_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "résultat en cache"
        verbose_name_plural = "cache IA"

    def __str__(self):
        return f"{self.get_type_display()} · {self.key[:10]} ({self.hits} réutilisation(s))"


class PipelineConfig(models.Model):
    """Réglages du pipeline (singleton, modifiable par les administrateurs)."""

    class WhisperModel(models.TextChoices):
        TURBO = "whisper-large-v3-turbo", "Whisper large v3 turbo (rapide)"
        LARGE = "whisper-large-v3", "Whisper large v3 (précis)"

    class Language(models.TextChoices):
        AUTO = "", "Détection automatique"
        FR = "fr", "Français"
        EN = "en", "Anglais"
        AR = "ar", "Arabe"

    describe_images = models.BooleanField("décrire les images", default=True)
    transcribe_audio = models.BooleanField("transcrire les audios", default=True)
    generate_tags = models.BooleanField("générer tags, thème et langue", default=True)
    summarize = models.BooleanField("résumer les audios et textes longs", default=True)
    max_tags = models.PositiveSmallIntegerField("nombre maximum de tags IA", default=5)
    whisper_model = models.CharField("modèle Whisper", max_length=40, choices=WhisperModel.choices, default=WhisperModel.TURBO)
    language_hint = models.CharField("langue des audios", max_length=5, choices=Language.choices, blank=True, default="")
    use_cache = models.BooleanField("réutiliser les analyses d'un même média", default=True)
    stuck_after_minutes = models.PositiveSmallIntegerField("analyse bloquée après (minutes)", default=10)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "configuration du pipeline"
        verbose_name_plural = "configuration du pipeline"

    def __str__(self):
        return "Configuration du pipeline IA"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Tag(models.Model):
    name = models.CharField("nom", max_length=50, unique=True)
    publications = models.ManyToManyField(
        "publications.Publication", related_name="tags", blank=True, through="PublicationTag"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "tag"
        verbose_name_plural = "tags"

    def __str__(self):
        return f"#{self.name}"


class PublicationTag(models.Model):
    class Source(models.TextChoices):
        AUTHOR = "auteur", "Auteur"
        AI = "ia", "IA"

    publication = models.ForeignKey("publications.Publication", on_delete=models.CASCADE, related_name="tag_links")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="links")
    source = models.CharField(max_length=6, choices=Source.choices, default=Source.AI)
    confidence = models.FloatField("confiance", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source", "-confidence"]
        verbose_name = "tag de publication"
        verbose_name_plural = "tags de publication"
        constraints = [models.UniqueConstraint(fields=["publication", "tag"], name="tag_unique_par_publication")]

    def __str__(self):
        return f"{self.tag} ({self.get_source_display()})"
