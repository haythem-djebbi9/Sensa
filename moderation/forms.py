from django import forms

from accounts.models import Membre

from .models import ConfigurationIA, Signalement


class ReportForm(forms.ModelForm):
    class Meta:
        model = Signalement
        fields = ("reason", "details")
        widgets = {
            "reason": forms.RadioSelect,
            "details": forms.Textarea(attrs={"rows": 3, "placeholder": "Ajoutez un contexte utile (facultatif)"}),
        }
        labels = {"reason": "Pourquoi signalez-vous ce contenu ?"}


class ConfigurationIAForm(forms.Form):
    review_percent = forms.IntegerField(
        label="Seuil de mise en revue",
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 1, "class": "range"}),
    )
    block_percent = forms.IntegerField(
        label="Seuil de blocage",
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={"type": "range", "min": 0, "max": 100, "step": 1, "class": "range"}),
    )
    max_retries = forms.IntegerField(label="Tentatives avant échec (FAILED)", min_value=1, max_value=10)

    def __init__(self, *args, config=None, **kwargs):
        self.config = config or ConfigurationIA.get_solo()
        kwargs.setdefault(
            "initial",
            {
                "review_percent": round(self.config.threshold_review * 100),
                "block_percent": round(self.config.threshold_block * 100),
                "max_retries": self.config.max_retries,
            },
        )
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        review, block = cleaned.get("review_percent"), cleaned.get("block_percent")
        if review is not None and block is not None and review >= block:
            raise forms.ValidationError("Le seuil de blocage doit être strictement supérieur au seuil de revue.")
        return cleaned

    def save(self, user):
        self.config.threshold_review = self.cleaned_data["review_percent"] / 100
        self.config.threshold_block = self.cleaned_data["block_percent"] / 100
        self.config.max_retries = self.cleaned_data["max_retries"]
        self.config.updated_by = user
        self.config.full_clean()
        self.config.save()
        return self.config


class AccountUpdateForm(forms.ModelForm):
    class Meta:
        model = Membre
        fields = ("role", "is_active")
