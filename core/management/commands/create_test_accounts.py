"""Comptes de test : un par rôle et un par membre de l'équipe.

    python manage.py create_test_accounts

Chaque compte a son propre mot de passe. Idempotent : relancer la commande réinitialise
le mot de passe et le rôle des comptes de test (les comptes réels ne sont jamais modifiés).
La liste est documentée dans docs/comptes_de_test.md.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Membre

ACCOUNTS = [
    # nom d'utilisateur, mot de passe, nom affiché, rôle, bio
    ("test_membre", "Membre#2026", "Test Membre", Membre.Role.MEMBRE, "Compte de test : membre."),
    ("test_moderateur", "Modo#2026", "Test Modérateur", Membre.Role.MODERATEUR, "Compte de test : modérateur."),
    ("test_admin", "Admin#2026", "Test Admin", Membre.Role.ADMIN, "Compte de test : administrateur."),
    ("hiba", "Hiba#Module1", "Hiba Chaabani", Membre.Role.ADMIN, "Module 1 · Publications"),
    ("mednour", "MedNour#Module2", "Med Nour Romdhani", Membre.Role.ADMIN, "Module 2 · Interactions sociales"),
    ("haythem", "Haythem#Module3", "Haythem Djebbi", Membre.Role.ADMIN, "Module 3 · Enrichissement IA asynchrone"),
    ("chedly", "Chedly#Module4", "Chedly Rebai", Membre.Role.ADMIN, "Module 4 · Modération et back-office"),
    ("taieb", "Taieb#Module5", "Taieb Bel Hadj Ali", Membre.Role.ADMIN, "Module 5 · Fil, recherche et recommandations"),
]


class Command(BaseCommand):
    help = "Crée (ou réinitialise) les comptes de test de Sensa."

    @transaction.atomic
    def handle(self, *args, **options):
        for username, password, name, role, bio in ACCOUNTS:
            member, created = Membre.objects.get_or_create(
                username=username, defaults={"email": f"{username}@sensa.test"}
            )
            if not created and not member.email.endswith("@sensa.test"):
                # Compte réel (créé par quelqu'un) : on n'y touche jamais.
                self.stdout.write(self.style.WARNING(f"  · {username:<16} ignoré : compte réel ({member.email})"))
                continue
            member.role = role
            member.is_staff = role == Membre.Role.ADMIN
            member.is_superuser = role == Membre.Role.ADMIN
            member.is_active = True
            member.email_confirmed = True
            member.set_password(password)
            member.save()
            member.profile.display_name = name
            member.profile.bio = bio
            member.profile.save(update_fields=["display_name", "bio"])
            state = "créé" if created else "réinitialisé"
            self.stdout.write(f"  · {username:<16} {member.get_role_display():<15} {state}")
        self.stdout.write(self.style.SUCCESS(f"{len(ACCOUNTS)} comptes de test prêts (voir docs/comptes_de_test.md)."))
