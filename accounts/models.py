"""Module commun : Membre, Moderateur, Administrateur et Profile."""

import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.urls import reverse


class Membre(AbstractUser):
    """Utilisateur inscrit. Le rôle détermine les permissions (Visiteur = non connecté)."""

    class Role(models.TextChoices):
        MEMBRE = "MEMBRE", "Membre"
        MODERATEUR = "MODERATEUR", "Modérateur"
        ADMIN = "ADMIN", "Administrateur"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("adresse e-mail", unique=True)
    role = models.CharField("rôle", max_length=12, choices=Role.choices, default=Role.MEMBRE, db_index=True)
    email_confirmed = models.BooleanField("e-mail confirmé", default=False)

    class Meta:
        verbose_name = "membre"
        verbose_name_plural = "membres"
        ordering = ["username"]

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_moderator(self):
        return self.is_superuser or self.role in {self.Role.MODERATEUR, self.Role.ADMIN}

    @property
    def is_admin_role(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def display_name(self):
        profile = getattr(self, "profile", None)
        return profile.name if profile else self.username

    def get_absolute_url(self):
        return reverse("accounts:profile", args=[self.username])


class ModerateurManager(UserManager):
    def get_queryset(self):
        return super().get_queryset().filter(role__in=[Membre.Role.MODERATEUR, Membre.Role.ADMIN])


class Moderateur(Membre):
    """Membre pouvant traiter les signalements et la file de modération (hérite de Membre)."""

    objects = ModerateurManager()

    class Meta:
        proxy = True
        verbose_name = "modérateur"
        verbose_name_plural = "modérateurs"


class AdministrateurManager(UserManager):
    def get_queryset(self):
        return super().get_queryset().filter(role=Membre.Role.ADMIN)


class Administrateur(Moderateur):
    """Modérateur qui gère aussi les comptes, les rôles et les seuils IA (hérite de Moderateur)."""

    objects = AdministrateurManager()

    class Meta:
        proxy = True
        verbose_name = "administrateur"
        verbose_name_plural = "administrateurs"


class Profile(models.Model):
    membre = models.OneToOneField(Membre, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField("nom affiché", max_length=80, blank=True)
    bio = models.TextField("bio", max_length=280, blank=True)
    avatar = models.ImageField("photo de profil", upload_to="avatars/%Y/%m/", blank=True)
    location = models.CharField("ville", max_length=80, blank=True)
    interests = models.CharField(
        "centres d'intérêt", max_length=200, blank=True, help_text="Séparés par des virgules, ex. : musique, randonnée"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profil"
        verbose_name_plural = "profils"

    def __str__(self):
        return f"Profil de {self.membre}"

    @property
    def name(self):
        return self.display_name or self.membre.username

    @property
    def interests_list(self):
        return [item.strip() for item in self.interests.split(",") if item.strip()]
