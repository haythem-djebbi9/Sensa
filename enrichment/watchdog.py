"""Chien de garde du pipeline (module 3).

Une analyse peut rester bloquée si un worker s'arrête en pleine tâche, ou si le
serveur redémarre pendant une relance programmée. Toutes les 5 minutes (Celery Beat),
ou à la demande depuis le tableau de bord, on remet ces analyses en file.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .dispatch import enqueue
from .models import AnalyseIA, PipelineConfig

logger = logging.getLogger("sensa.ai")
S = AnalyseIA.Status


def stuck_queryset():
    config = PipelineConfig.get_solo()
    now = timezone.now()
    running_limit = now - timedelta(minutes=config.stuck_after_minutes)
    retry_limit = now - timedelta(seconds=max(settings.SENSA_RETRY_BACKOFF) + 120)
    running = AnalyseIA.objects.filter(status=S.RUNNING, updated_at__lt=running_limit)
    retry = AnalyseIA.objects.filter(status=S.RETRY, updated_at__lt=retry_limit)
    return running | retry


def _publications_with_lost_pending(limit):
    """Publications dont des analyses attendent alors que plus rien n'avance."""
    stale = AnalyseIA.objects.filter(status=S.PENDING, updated_at__lt=limit).exclude(type__in=AnalyseIA.SIDE_TYPES)
    busy = set(
        AnalyseIA.objects.filter(status__in=[S.RUNNING, S.RETRY], updated_at__gte=limit).values_list("publication_id", flat=True)
    )
    return {pk for pk in stale.values_list("publication_id", flat=True) if pk not in busy}


def requeue_stuck():
    """Remet en file les analyses bloquées ; renvoie le nombre d'analyses relancées."""
    from publications.models import Publication
    from social.tasks import analyse_tone

    from .pipeline import advance
    from .tasks import translate_transcription

    limit = timezone.now() - timedelta(minutes=PipelineConfig.get_solo().stuck_after_minutes)
    rows = list(stuck_queryset())
    publications = set()
    for row in rows:
        row.status = S.PENDING
        row.save(update_fields=["status", "updated_at"])
        if row.type == AnalyseIA.Type.TONE:
            enqueue(analyse_tone, [int(row.target_id)])
        elif row.type == AnalyseIA.Type.TRANSLATION:
            audio_id, language = row.target_id.split(":", 1)
            enqueue(translate_transcription, [int(audio_id), language])
        else:
            publications.add(row.publication_id)
    lost = _publications_with_lost_pending(limit)
    publications |= lost
    for publication_id in publications:
        Publication.objects.filter(pk=publication_id).update(status=Publication.Status.PENDING)
        advance(publication_id)
    count = len(rows) + len(lost)
    if count:
        logger.info("Chien de garde : %s analyse(s) ou pipeline(s) relancé(s)", count)
    return count
