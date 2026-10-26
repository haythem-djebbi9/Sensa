from datetime import timedelta

from django import forms
from django.utils import timezone

from core.ai.text import slug_tag
from publications.validators import validate_audio_upload, validate_image_upload

from . import lab, prompts
from .models import PromptIA, Tag, VocabulaireIA


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ("name", "description", "status")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "ex. randonnee"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "À quoi sert ce tag ? (facultatif)"}),
        }
        help_texts = {"name": "Minuscules, sans # ni accents ni espaces (normalisé automatiquement)."}

    def clean_name(self):
        name = slug_tag(self.cleaned_data["name"])
        if len(name) < 2:
            raise forms.ValidationError("Le nom doit contenir au moins 2 lettres ou chiffres.")
        if Tag.objects.filter(name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(f"Le tag #{name} existe déjà : utilisez plutôt la fusion.")
        return name


class TagMergeForm(forms.Form):
    target = forms.ModelChoiceField(
        label="Fusionner dans", queryset=Tag.objects.none(), empty_label="Choisissez le tag à conserver"
    )

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Tag.objects.exclude(pk=source.pk).order_by("name")
        self.fields["target"].label_from_instance = lambda tag: f"#{tag.name}"


class VocabulaireForm(forms.ModelForm):
    class Meta:
        model = VocabulaireIA
        fields = ("term", "variants", "category", "is_active")
        widgets = {
            "term": forms.TextInput(attrs={"placeholder": "ex. Aïn Draham"}),
            "variants": forms.TextInput(attrs={"placeholder": "ex. ain drahem, aïn drahem"}),
        }

    def clean_term(self):
        term = " ".join(self.cleaned_data["term"].split())
        if VocabulaireIA.objects.filter(term__iexact=term).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Ce terme existe déjà dans le vocabulaire.")
        return term

    def clean_variants(self):
        variants = [v.strip() for v in self.cleaned_data.get("variants", "").split(",") if v.strip()]
        return ", ".join(dict.fromkeys(variants))


class PromptForm(forms.ModelForm):
    activate_now = forms.BooleanField(label="Activer cette version dès l'enregistrement", required=False)

    class Meta:
        model = PromptIA
        fields = ("task", "name", "template", "notes")
        widgets = {
            "template": forms.Textarea(attrs={"rows": 14, "class": "code-input"}),
            "notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Pourquoi cette version ? (facultatif)"}),
            "name": forms.TextInput(attrs={"placeholder": "ex. Tags plus courts"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["task"].disabled = True

    def clean(self):
        cleaned = super().clean()
        task, template = cleaned.get("task"), cleaned.get("template")
        if task and template:
            try:
                prompts.validate(task, template)
            except forms.ValidationError as exc:
                self.add_error("template", exc)
        return cleaned


class LabForm(forms.Form):
    task = forms.ChoiceField(label="Tâche à tester", choices=lab.TASKS)
    text = forms.CharField(
        label="Texte", required=False, max_length=4000,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Collez un texte, une transcription ou un commentaire…"}),
    )
    image = forms.ImageField(label="Image", required=False, widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
    audio = forms.FileField(label="Audio", required=False, widget=forms.FileInput(attrs={"accept": "audio/*"}))
    language = forms.ChoiceField(label="Langue cible (traduction)", choices=[("en", "Anglais"), ("ar", "Arabe"), ("fr", "Français")])
    prompt = forms.ModelChoiceField(
        label="Version du prompt", required=False, queryset=PromptIA.objects.all(),
        empty_label="Version active (ou prompt par défaut)",
    )

    def clean(self):
        cleaned = super().clean()
        task = cleaned.get("task")
        if task in lab.TEXT_TASKS and not (cleaned.get("text") or "").strip():
            self.add_error("text", "Saisissez un texte pour cette tâche.")
        if task == "description":
            image = cleaned.get("image")
            if not image:
                self.add_error("image", "Choisissez une image.")
            else:
                validate_image_upload(image)
        if task == "transcription":
            audio = cleaned.get("audio")
            if not audio:
                self.add_error("audio", "Choisissez un fichier audio.")
            else:
                validate_audio_upload(audio)
        prompt = cleaned.get("prompt")
        if prompt and task in lab.PROMPT_TASKS and prompt.task != task:
            self.add_error("prompt", "Cette version de prompt correspond à une autre tâche.")
        if task not in lab.PROMPT_TASKS:
            cleaned["prompt"] = None
        return cleaned


class DigestForm(forms.Form):
    period_start = forms.DateField(label="Du", widget=forms.DateInput(attrs={"type": "date"}))
    period_end = forms.DateField(label="Au", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        today = timezone.localdate()
        kwargs.setdefault("initial", {"period_start": today - timedelta(days=7), "period_end": today})
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end:
            if start > end:
                raise forms.ValidationError("La date de début doit précéder la date de fin.")
            if (end - start).days > 92:
                raise forms.ValidationError("La période ne peut pas dépasser trois mois.")
        return cleaned
