"""Module 1 — Publications (Hiba Chaabani) : Publication, Image, Audio, Republication, Enregistrement."""

import io
import os
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse


class PublicationQuerySet(models.QuerySet):
    #: Statuts visibles publiquement (UNDER_REVIEW et BLOCKED ne le sont pas).
    PUBLIC_STATUSES = ("PENDING", "READY", "FAILED")

    def visible_to(self, user):
        """Publications qu'un utilisateur a le droit de voir (les brouillons ne sont visibles nulle part)."""
        if not user.is_authenticated:
            return self.filter(status="READY", visibility="PUBLIC")
        queryset = self.exclude(status="DRAFT")
        if user.is_moderator:
            return queryset
        from social.models import Abonnement

        follows = Abonnement.objects.filter(follower=user, followed=OuterRef("author"))
        return queryset.annotate(_followed=Exists(follows)).filter(
            Q(author=user)
            | Q(status__in=self.PUBLIC_STATUSES, visibility="PUBLIC")
            | Q(status__in=self.PUBLIC_STATUSES, visibility="FOLLOWERS", _followed=True)
        )

    def for_cards(self):
        """Pré-charge tout ce que la carte d'un post affiche (dont les enrichissements du module 3)."""
        from django.db.models import Prefetch

        from enrichment.models import AnalyseIA, PublicationTag

        return self.select_related("author", "author__profile", "audio").prefetch_related(
            "images",
            "tags",
            Prefetch("tag_links", queryset=PublicationTag.objects.select_related("tag").order_by("source", "-confidence")),
            Prefetch(
                "analyses",
                queryset=AnalyseIA.objects.filter(
                    type__in=[AnalyseIA.Type.TAGS, AnalyseIA.Type.SUMMARY], status=AnalyseIA.Status.DONE
                ),
                to_attr="ai_insights",
            ),
        )


class Publication(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "En attente d'analyse"
        READY = "READY", "Prêt"
        UNDER_REVIEW = "UNDER_REVIEW", "En revue"
        FAILED = "FAILED", "Échec de l'analyse IA"
        BLOCKED = "BLOCKED", "Bloqué"
        DRAFT = "DRAFT", "Brouillon"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        FOLLOWERS = "FOLLOWERS", "Abonnés uniquement"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="publications", verbose_name="auteur"
    )
    text = models.TextField("texte", max_length=2000, blank=True)
    status = models.CharField("statut", max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    visibility = models.CharField("visibilité", max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    created_at = models.DateTimeField("créée le", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)
    # Module 5 (Taieb) : vecteur sémantique du texte + textes IA, calculé par feed.tasks.embed_post.
    # Seul champ ajouté par un autre module à ce modèle partagé (convenu avec le module 1).
    embedding = models.JSONField("embedding", null=True, blank=True, editable=False)

    objects = PublicationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "publication"
        verbose_name_plural = "publications"
        indexes = [models.Index(fields=["author", "-created_at"])]

    def __str__(self):
        excerpt = (self.text or "").strip().replace("\n", " ")
        return f"{self.author} · {excerpt[:40] or 'publication média'}"

    def get_absolute_url(self):
        # Un brouillon n'a pas de page publique : on l'ouvre pour le terminer.
        if self.is_draft:
            return reverse("publications:edit", args=[self.pk])
        return reverse("publications:detail", args=[self.pk])

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def audio_or_none(self):
        try:
            return self.audio
        except Audio.DoesNotExist:
            return None

    @property
    def main_image(self):
        images = list(self.images.all())
        return images[0] if images else None

    @property
    def has_ai_content(self):
        if any(image.description_ia for image in self.images.all()):
            return True
        audio = self.audio_or_none
        return bool(audio and audio.transcription)

    @property
    def is_public(self):
        return self.status in PublicationQuerySet.PUBLIC_STATUSES

    def is_visible_to(self, user):
        return Publication.objects.visible_to(user).filter(pk=self.pk).exists()

    def can_edit(self, user):
        return user.is_authenticated and user.pk == self.author_id

    def can_delete(self, user):
        return user.is_authenticated and (user.pk == self.author_id or user.is_moderator)

    @property
    def is_republishable(self):
        """Seules les publications publiques déjà validées par l'IA peuvent être republiées."""
        return self.status == self.Status.READY and self.visibility == self.Visibility.PUBLIC

    def can_republish(self, user):
        return user.is_authenticated and self.is_republishable

    @property
    def excerpt(self):
        text = (self.text or "").strip().replace("\n", " ")
        return text[:90] + ("…" if len(text) > 90 else "")


def build_display_version(field_file, max_side=1280):
    """Version allégée d'une image (WebP, `max_side` pixels au plus), ou None pour une image animée."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    with field_file.open("rb") as handle:
        with PILImage.open(handle) as img:
            if getattr(img, "n_frames", 1) > 1:
                return None  # on garde l'original pour ne pas perdre l'animation
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            img.thumbnail((max_side, max_side))
            buffer = io.BytesIO()
            img.save(buffer, format="WEBP", quality=80)
    stem = os.path.splitext(os.path.basename(field_file.name))[0]
    return ContentFile(buffer.getvalue(), name=f"{stem}-apercu.webp")


class Image(models.Model):
    publication = models.ForeignKey(Publication, on_delete=models.CASCADE, related_name="images")
    file = models.ImageField("fichier", upload_to="publications/images/%Y/%m/", width_field="width", height_field="height")
    width = models.PositiveIntegerField("largeur (px)", null=True, editable=False)
    height = models.PositiveIntegerField("hauteur (px)", null=True, editable=False)
    # Version allégée affichée dans le fil et les vignettes ; l'original reste sur la page de détail.
    display = models.ImageField("version allégée", upload_to="publications/images/%Y/%m/", blank=True, editable=False)
    alt_text = models.CharField("texte alternatif (auteur)", max_length=300, blank=True)
    # Écrit par la tâche IA describe_image (module 1).
    description_ia = models.TextField("description IA", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "image"
        verbose_name_plural = "images"

    def __str__(self):
        return f"Image de {self.publication_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.file and not self.display:
            display = build_display_version(self.file)
            if display is not None:
                self.display.save(display.name, display, save=False)
                super().save(update_fields=["display"])

    @property
    def display_url(self):
        return (self.display or self.file).url

    # Proportions (largeur / hauteur) affichées dans le fil, comme Instagram : de 4:5 en portrait à 1,91:1 en paysage.
    MIN_RATIO, MAX_RATIO = 4 / 5, 1.91

    @property
    def aspect_ratio(self):
        """Proportions de l'image dans le fil, sous forme de texte pour la propriété CSS aspect-ratio."""
        if not self.width or not self.height:
            return "4 / 3"
        ratio = min(max(self.width / self.height, self.MIN_RATIO), self.MAX_RATIO)
        return f"{ratio:.4f}"

    @property
    def alt(self):
        """Texte alternatif affiché : celui de l'auteur, sinon la description IA."""
        return self.alt_text or self.description_ia or "Image publiée sur Sensa (description en cours)"


class Audio(models.Model):
    publication = models.OneToOneField(Publication, on_delete=models.CASCADE, related_name="audio")
    file = models.FileField("fichier", upload_to="publications/audio/%Y/%m/")
    duration = models.FloatField("durée (s)", default=0)
    # Écrits par la tâche transcribe_audio (module 3).
    transcription = models.TextField("transcription", blank=True)
    language = models.CharField("langue détectée", max_length=12, blank=True)
    segments = models.JSONField("segments horodatés", default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "audio"
        verbose_name_plural = "audios"

    def __str__(self):
        return f"Audio de {self.publication_id}"

    @property
    def transcript_lines(self):
        """Lignes horodatées pour l'affichage, à défaut le texte brut."""
        if self.segments:
            return [
                {"start": seg.get("start", 0), "text": (seg.get("text") or "").strip()}
                for seg in self.segments
                if (seg.get("text") or "").strip()
            ]
        if self.transcription:
            return [{"start": 0, "text": self.transcription}]
        return []


class Republication(models.Model):
    """Republication d'un post par un membre (l'auteur compris), avec un court message facultatif.
    Le post original est repartagé tel quel, sans copie : il disparaît des republications s'il n'est plus visible."""

    MESSAGE_MAX_LENGTH = 200

    publication = models.ForeignKey(Publication, on_delete=models.CASCADE, related_name="republications")
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="republications", verbose_name="membre"
    )
    message = models.CharField("message", max_length=MESSAGE_MAX_LENGTH, blank=True)
    created_at = models.DateTimeField("republiée le", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "republication"
        verbose_name_plural = "republications"
        constraints = [models.UniqueConstraint(fields=["publication", "member"], name="republication_unique")]

    def __str__(self):
        return f"{self.member} republie {self.publication_id}"


class Enregistrement(models.Model):
    """Publication enregistrée par un membre pour la retrouver plus tard (visible par lui seul)."""

    publication = models.ForeignKey(Publication, on_delete=models.CASCADE, related_name="enregistrements")
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enregistrements", verbose_name="membre"
    )
    created_at = models.DateTimeField("enregistrée le", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "enregistrement"
        verbose_name_plural = "enregistrements"
        constraints = [models.UniqueConstraint(fields=["publication", "member"], name="enregistrement_unique")]

    def __str__(self):
        return f"{self.member} enregistre {self.publication_id}"
