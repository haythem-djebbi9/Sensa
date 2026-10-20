from django import forms
from django.conf import settings
from django.db import transaction

from .models import Audio, Image, Publication
from .validators import read_audio_duration, validate_audio_upload, validate_image_upload


class PublicationForm(forms.ModelForm):
    image = forms.ImageField(
        label="Image",
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "data-image-input": ""}),
    )
    alt_text = forms.CharField(
        label="Texte alternatif (facultatif)",
        required=False,
        max_length=300,
        widget=forms.TextInput(attrs={"placeholder": "Décrivez l'image — sinon l'IA s'en charge"}),
    )
    audio = forms.FileField(
        label="Audio",
        required=False,
        widget=forms.FileInput(attrs={"accept": "audio/*", "data-audio-input": ""}),
    )
    audio_duration = forms.FloatField(required=False, min_value=0, widget=forms.HiddenInput(attrs={"data-audio-duration": ""}))

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

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image:
            validate_image_upload(image)
        return image

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
            measured = read_audio_duration(audio)
            reported = cleaned.get("audio_duration")
            seconds = measured if measured is not None else reported
            if seconds is None:
                self.add_error("audio", "Durée audio illisible : utilisez un fichier MP3, M4A, OGG ou WAV.")
            elif seconds > settings.SENSA_MAX_AUDIO_SECONDS + 1:
                self.add_error("audio", "L'audio ne doit pas dépasser 3 minutes.")
            else:
                self.audio_seconds = round(float(seconds), 2)
        cleaned["text"] = text
        return cleaned

    @transaction.atomic
    def save_for(self, author):
        publication = super().save(commit=False)
        publication.author = author
        publication.status = Publication.Status.PENDING
        publication.save()
        image = self.cleaned_data.get("image")
        if image:
            Image.objects.create(publication=publication, file=image, alt_text=self.cleaned_data.get("alt_text", ""))
        audio = self.cleaned_data.get("audio")
        if audio:
            Audio.objects.create(publication=publication, file=audio, duration=self.audio_seconds)
        return publication


class PublicationEditForm(forms.ModelForm):
    alt_text = forms.CharField(label="Texte alternatif de l'image", required=False, max_length=300)

    class Meta:
        model = Publication
        fields = ("text", "visibility")
        widgets = {"text": forms.Textarea(attrs={"maxlength": settings.SENSA_MAX_TEXT_LENGTH, "data-char-source": ""})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.image = self.instance.main_image
        if self.image is None:
            del self.fields["alt_text"]
        else:
            self.fields["alt_text"].initial = self.image.alt_text

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("text") or "").strip()
        has_media = self.image is not None or self.instance.audio_or_none is not None
        if not text and not has_media:
            raise forms.ValidationError("Une publication sans média doit contenir du texte.")
        cleaned["text"] = text
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        publication = super().save(commit=commit)
        if self.image is not None and "alt_text" in self.changed_data:
            self.image.alt_text = self.cleaned_data.get("alt_text", "")
            self.image.save(update_fields=["alt_text"])
        return publication
