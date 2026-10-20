from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import Membre, Profile


class SignupForm(UserCreationForm):
    display_name = forms.CharField(
        label="Nom", max_length=80, required=False, widget=forms.TextInput(attrs={"placeholder": "Votre nom"})
    )
    email = forms.EmailField(label="Adresse e-mail", widget=forms.EmailInput(attrs={"placeholder": "vous@exemple.com"}))

    class Meta:
        model = Membre
        fields = ("display_name", "username", "email")
        widgets = {"username": forms.TextInput(attrs={"placeholder": "ex. lina.brooks"})}
        labels = {"username": "Nom d'utilisateur"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].widget.attrs["placeholder"] = "Au moins 8 caractères"
        self.fields["password2"].widget.attrs["placeholder"] = "Répétez le mot de passe"
        self.fields["username"].help_text = "Lettres, chiffres et . _ - uniquement."
        self.fields["password1"].help_text = ""

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if Membre.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse e-mail.")
        return email

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.profile.display_name = self.cleaned_data.get("display_name", "")
            user.profile.save(update_fields=["display_name"])
        return user


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="E-mail ou nom d'utilisateur",
        widget=forms.TextInput(attrs={"autofocus": True, "placeholder": "vous@exemple.com", "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Mot de passe",
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Votre mot de passe", "autocomplete": "current-password"}),
    )
    error_messages = {
        "invalid_login": "Identifiants incorrects. Vérifiez votre e-mail (ou nom d'utilisateur) et votre mot de passe.",
        "inactive": "Ce compte est désactivé.",
    }


class AccountForm(forms.ModelForm):
    class Meta:
        model = Membre
        fields = ("email",)
        labels = {"email": "Adresse e-mail"}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if Membre.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return email


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("display_name", "bio", "location", "interests", "avatar")
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3, "placeholder": "Quelques mots sur vous…"}),
            "avatar": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and hasattr(avatar, "size") and avatar.size > settings.SENSA_MAX_UPLOAD_BYTES:
            raise forms.ValidationError("La photo ne doit pas dépasser 10 Mo.")
        return avatar
