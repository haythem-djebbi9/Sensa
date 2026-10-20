from django.apps import AppConfig


class PublicationsConfig(AppConfig):
    name = "publications"
    verbose_name = "1. Publications"

    def ready(self):
        from . import signals  # noqa: F401
