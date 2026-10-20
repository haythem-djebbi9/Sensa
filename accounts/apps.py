from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Comptes et profils (commun)"

    def ready(self):
        from . import signals  # noqa: F401
