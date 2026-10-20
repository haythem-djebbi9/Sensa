from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    """Jeton de confirmation d'e-mail, invalidé dès que l'adresse est confirmée ou modifiée."""

    key_salt = "sensa.accounts.EmailConfirmationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{timestamp}{user.email}{user.email_confirmed}"


email_confirmation_token = EmailConfirmationTokenGenerator()
