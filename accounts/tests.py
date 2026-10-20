from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.testing import make_member

from .models import Administrateur, Membre, Moderateur
from .tokens import email_confirmation_token


class MembreModelTests(TestCase):
    def test_profile_created_and_roles(self):
        member = make_member("lina")
        moderator = make_member("camille", role=Membre.Role.MODERATEUR)
        admin = make_member("avery", role=Membre.Role.ADMIN)
        self.assertTrue(hasattr(member, "profile"))
        self.assertFalse(member.is_moderator)
        self.assertTrue(moderator.is_moderator)
        self.assertFalse(moderator.is_admin_role)
        self.assertTrue(admin.is_moderator and admin.is_admin_role)
        # Héritage : Administrateur ⊂ Moderateur ⊂ Membre
        self.assertEqual(set(Moderateur.objects.values_list("username", flat=True)), {"camille", "avery"})
        self.assertEqual(list(Administrateur.objects.values_list("username", flat=True)), ["avery"])

    def test_superuser_is_admin(self):
        user = Membre.objects.create_superuser("root", "root@test.local", "MotDePasse-Test-123")
        self.assertEqual(user.role, Membre.Role.ADMIN)


class AuthFlowTests(TestCase):
    def test_signup_logs_in_and_sends_confirmation(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "display_name": "Lina Brooks",
                "username": "lina",
                "email": "Lina@Test.local",
                "password1": "Un-Mot-De-Passe-Solide-42",
                "password2": "Un-Mot-De-Passe-Solide-42",
            },
        )
        self.assertRedirects(response, reverse("feed:home"))
        member = Membre.objects.get(username="lina")
        self.assertEqual(member.email, "lina@test.local")
        self.assertEqual(member.profile.display_name, "Lina Brooks")
        self.assertFalse(member.email_confirmed)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/comptes/confirmer/", mail.outbox[0].body)

    def test_duplicate_email_rejected(self):
        make_member("lina")
        response = self.client.post(
            reverse("accounts:signup"),
            {"username": "autre", "email": "lina@test.local", "password1": "Un-Mot-De-Passe-Solide-42",
             "password2": "Un-Mot-De-Passe-Solide-42"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "existe déjà")

    def test_confirm_email(self):
        member = make_member("lina")
        uid = urlsafe_base64_encode(force_bytes(member.pk))
        token = email_confirmation_token.make_token(member)
        self.client.get(reverse("accounts:confirm_email", args=[uid, token]))
        member.refresh_from_db()
        self.assertTrue(member.email_confirmed)
        # Le jeton ne sert qu'une fois.
        self.assertFalse(email_confirmation_token.check_token(member, token))

    def test_login_with_email_or_username(self):
        make_member("lina")
        for identifier in ("lina", "LINA@test.local"):
            client = self.client_class()
            response = client.post(reverse("accounts:login"), {"username": identifier, "password": "MotDePasse-Test-123"})
            self.assertRedirects(response, reverse("feed:home"), fetch_redirect_response=False)

    def test_logout_requires_post(self):
        self.client.force_login(make_member("lina"))
        response = self.client.post(reverse("accounts:logout"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("feed:search")).status_code, 302)


class RolePermissionTests(TestCase):
    def test_visitor_member_moderator_admin(self):
        urls = {
            "member": reverse("feed:search"),
            "moderator": reverse("moderation:queue"),
            "admin": reverse("moderation:settings"),
        }
        self.assertEqual(self.client.get(urls["member"]).status_code, 302)  # visiteur → connexion
        self.assertEqual(self.client.get(reverse("feed:home")).status_code, 200)  # page d'accueil publique

        self.client.force_login(make_member("lina"))
        self.assertEqual(self.client.get(urls["member"]).status_code, 200)
        self.assertEqual(self.client.get(urls["moderator"]).status_code, 403)

        self.client.force_login(make_member("camille", role=Membre.Role.MODERATEUR))
        self.assertEqual(self.client.get(urls["moderator"]).status_code, 200)
        self.assertEqual(self.client.get(urls["admin"]).status_code, 403)

        self.client.force_login(make_member("avery", role=Membre.Role.ADMIN))
        self.assertEqual(self.client.get(urls["admin"]).status_code, 200)

    def test_profile_edit(self):
        member = make_member("lina")
        self.client.force_login(member)
        response = self.client.post(
            reverse("accounts:profile_edit"),
            {"compte-email": "nouvelle@test.local", "profil-display_name": "Lina B.", "profil-bio": "Bonjour",
             "profil-location": "Tunis", "profil-interests": "musique, photo"},
        )
        self.assertRedirects(response, reverse("accounts:profile", args=["lina"]))
        member.refresh_from_db()
        self.assertEqual(member.profile.interests_list, ["musique", "photo"])
        self.assertFalse(member.email_confirmed)
        self.assertEqual(len(mail.outbox), 1)
