import io

from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from .models import Audio, Image, Publication
from .validators import read_audio_duration, validate_audio_upload, validate_image_upload


def strip_image_metadata(upload):
    """Ré-encode l'image sans ses métadonnées EXIF (position GPS, appareil…), orientation appliquée."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    upload.seek(0)
    buffer = io.BytesIO()
    with PILImage.open(upload) as img:
        image_format = img.format
        options = {"icc_profile": img.info.get("icc_profile")}
        if image_format in ("JPEG", "WEBP"):
            options["quality"] = 90
        if getattr(img, "n_frames", 1) > 1:
            img.save(buffer, format=image_format, save_all=True, **options)
        else:
            ImageOps.exif_transpose(img).save(buffer, format=image_format, **options)
    return ContentFile(buffer.getvalue(), name=upload.name)


class MultipleImageInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    """Plusieurs images dans un même champ (carrousel) : renvoie une liste, vide si aucune."""

    def __init__(self, *args, **kwargs):
        attrs = {"accept": "image/jpeg,image/png,image/webp", "data-image-input": "",
                 "data-max-files": settings.SENSA_MAX_IMAGES}
        kwargs.setdefault("widget", MultipleImageInput(attrs=attrs))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        images = [super(MultipleImageField, self).clean(file, initial) for file in files if file]
        if len(images) > settings.SENSA_MAX_IMAGES:
            raise forms.ValidationError(f"{settings.SENSA_MAX_IMAGES} images maximum par publication.")
        for image in images:
            validate_image_upload(image)
        return images


def measure_audio(audio):
    """Durée de l'audio en secondes, mesurée sur le fichier : celle annoncée par le navigateur est modifiable."""
    seconds = read_audio_duration(audio)
    if seconds is None:
        raise forms.ValidationError("Durée audio illisible : utilisez un fichier WebM, MP3, M4A, OGG ou WAV.")
    if seconds > settings.SENSA_MAX_AUDIO_SECONDS + 1:
        raise forms.ValidationError("L'audio ne doit pas dépasser 3 minutes.")
    return round(float(seconds), 2)


class PublicationForm(forms.ModelForm):
    image = MultipleImageField(label="Images", required=False)
    alt_text = forms.CharField(
        label="Texte alternatif",
        required=False,
        max_length=300,
        widget=forms.TextInput(attrs={"placeholder": "Décrivez l'image — sinon l'IA s'en charge"}),
    )
    audio = forms.FileField(
        label="Audio",
        required=False,
        widget=forms.FileInput(attrs={"accept": "audio/*", "data-audio-input": ""}),
    )

    class Meta:
        model = Publication
        fields = ("text", "visibility")
        widgets = {
            "text": forms.Textarea(
                attrs={
                    "placeholder": "Que se passe-t-il autour de vous ? #hashtags bienvenus",
                    "maxlength": settings.SENSA_MAX_TEXT_LENGTH,
                    "data-char-source": "",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.audio_seconds = 0.0

    def clean_audio(self):
        audio = self.cleaned_data.get("audio")
        if audio:
            validate_audio_upload(audio)
        return audio

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("text") or "").strip()
        image = cleaned.get("image")
        audio = cleaned.get("audio")
        if not (text or image or audio) and not self.errors:
            raise forms.ValidationError("Ajoutez un texte, une image ou un audio avant de publier.")
        if audio:
            try:
                self.audio_seconds = measure_audio(audio)
            except forms.ValidationError as error:
                self.add_error("audio", error)
        cleaned["text"] = text
        return cleaned

    @transaction.atomic
    def save_for(self, author):
        publication = super().save(commit=False)
        publication.author = author
        publication.status = Publication.Status.PENDING
        publication.save()
        for position, image in enumerate(self.cleaned_data.get("image") or []):
            # Le texte alternatif saisi décrit la première image ; l'IA décrit les autres.
            alt_text = self.cleaned_data.get("alt_text", "") if position == 0 else ""
            Image.objects.create(publication=publication, file=strip_image_metadata(image), alt_text=alt_text)
        audio = self.cleaned_data.get("audio")
        if audio:
            Audio.objects.create(publication=publication, file=audio, duration=self.audio_seconds)
        return publication


class PublicationEditForm(forms.ModelForm):
    alt_text = forms.CharField(label="Texte alternatif de la première image", required=False, max_length=300)
    remove_images = forms.ModelMultipleChoiceField(
        label="Images à retirer", queryset=Image.objects.none(), required=False, widget=forms.CheckboxSelectMultiple
    )
    new_images = MultipleImageField(label="Ajouter des images", required=False)
    remove_audio = forms.BooleanField(label="Retirer l'audio", required=False)
    audio = forms.FileField(
        label="Remplacer l'audio", required=False, widget=forms.FileInput(attrs={"accept": "audio/*"})
    )

    class Meta:
        model = Publication
        fields = ("text", "visibility")
        widgets = {"text": forms.Textarea(attrs={"maxlength": settings.SENSA_MAX_TEXT_LENGTH, "data-char-source": ""})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.images = list(Image.objects.filter(publication=self.instance))
        self.current_audio = Audio.objects.filter(publication=self.instance).first()
        self.audio_seconds = 0.0
        self.media_changed = False
        self.fields["remove_images"].queryset = Image.objects.filter(publication=self.instance)
        if self.images:
            self.fields["alt_text"].initial = self.images[0].alt_text
        else:
            del self.fields["alt_text"]
            del self.fields["remove_images"]
        if self.current_audio is None:
            del self.fields["remove_audio"]
            self.fields["audio"].label = "Ajouter un audio"

    def clean_audio(self):
        audio = self.cleaned_data.get("audio")
        if audio:
            validate_audio_upload(audio)
            self.audio_seconds = measure_audio(audio)
        return audio

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("text") or "").strip()
        removed = set(cleaned.get("remove_images") or [])
        kept_images = [image for image in self.images if image not in removed]
        new_images = cleaned.get("new_images") or []
        if len(kept_images) + len(new_images) > settings.SENSA_MAX_IMAGES:
            self.add_error("new_images", f"{settings.SENSA_MAX_IMAGES} images maximum par publication.")
        has_audio = bool(cleaned.get("audio")) or (self.current_audio is not None and not cleaned.get("remove_audio"))
        if not text and not kept_images and not new_images and not has_audio and not self.errors:
            raise forms.ValidationError("Une publication sans média doit contenir du texte.")
        cleaned["text"] = text
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        from enrichment.models import AnalyseIA

        publication = super().save(commit=commit)
        analyses = AnalyseIA.objects.filter(publication=publication)
        removed = list(self.cleaned_data.get("remove_images") or [])
        for image in removed:
            analyses.filter(type=AnalyseIA.Type.DESCRIPTION, target_id=str(image.pk)).delete()
            image.delete()
        for image in self.cleaned_data.get("new_images") or []:
            Image.objects.create(publication=publication, file=strip_image_metadata(image))
        new_audio = self.cleaned_data.get("audio")
        if self.current_audio is not None and (new_audio or self.cleaned_data.get("remove_audio")):
            analyses.filter(type=AnalyseIA.Type.TRANSCRIPTION, target_id=str(self.current_audio.pk)).delete()
            analyses.filter(type=AnalyseIA.Type.SUMMARY).delete()  # le résumé portait sur l'ancien audio
            self.current_audio.delete()
        if new_audio:
            Audio.objects.create(publication=publication, file=new_audio, duration=self.audio_seconds)
        self.media_changed = bool(removed or self.cleaned_data.get("new_images") or new_audio
                                  or self.cleaned_data.get("remove_audio"))
        if self.images and "alt_text" in self.changed_data and self.images[0] not in removed:
            self.images[0].alt_text = self.cleaned_data.get("alt_text", "")
            self.images[0].save(update_fields=["alt_text"])
        return publication
