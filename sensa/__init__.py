# Charge l'application Celery au démarrage de Django (pour @shared_task).
from .celery import app as celery_app

__all__ = ("celery_app",)
