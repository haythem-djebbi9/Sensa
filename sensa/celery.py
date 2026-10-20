"""Application Celery partagée par tous les modules (mise en place : module 3)."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sensa.settings")

app = Celery("sensa")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
