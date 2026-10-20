"""Orchestration du pipeline IA d'une publication (module 3).

Publier tout de suite, enrichir ensuite : la publication est enregistrée en
PENDING, puis les analyses s'enchaînent en tâche de fond, dans cet ordre :
description d'image → transcription → tags/thème/langue → résumé → modération → embedding.
Le statut final est calculé par `finalize` : READY, UNDER_REVIEW, BLOCKED ou FAILED.
Les étapes facultatives se règlent dans PipelineConfig (la modération et l'embedding
sont toujours exécutés : sécurité et fil).
"""

import logging

from django.db import transaction
from django.utils.module_loading import import_string

from .dispatch import enqueue
from .models import AnalyseIA, PipelineConfig

logger = logging.getLogger("sensa.ai")

T = AnalyseIA.Type
S = AnalyseIA.Status

ORDER = [T.DESCRIPTION, T.TRANSCRIPTION, T.TAGS, T.SUMMARY, T.MODERATION, T.EMBEDDING]

# Tâche Celery de chaque étape, appelée avec l'identifiant de sa cible.
STEP_TASKS = {
    T.DESCRIPTION: "publications.tasks.describe_image",  # module 1
    T.TRANSCRIPTION: "enrichment.tasks.transcribe_audio",  # module 3
    T.TAGS: "enrichment.tasks.generate_tags",  # module 3
    T.SUMMARY: "enrichment.tasks.summarize_post",  # module 3
    T.MODERATION: "moderation.tasks.moderate_publication",  # module 4
    T.EMBEDDING: "feed.tasks.embed_post",  # module 5
}

#: Un texte plus long que cela reçoit un résumé, même sans audio.
SUMMARY_MIN_CHARS = 280


def needs_summary(publication):
    return publication.audio_or_none is not None or len(publication.text or "") >= SUMMARY_MIN_CHARS


def plan_steps(publication, types=None):
    config = PipelineConfig.get_solo()

    def wanted(step, enabled=True):
        return enabled and (types is None or step in types)

    steps = []
    if wanted(T.DESCRIPTION, config.describe_images):
        steps += [(T.DESCRIPTION, str(image.pk)) for image in publication.images.all()]
    audio = publication.audio_or_none
    if audio is not None and wanted(T.TRANSCRIPTION, config.transcribe_audio):
        steps.append((T.TRANSCRIPTION, str(audio.pk)))
    if wanted(T.TAGS, config.generate_tags):
        steps.append((T.TAGS, str(publication.pk)))
    if wanted(T.SUMMARY, config.summarize) and needs_summary(publication):
        steps.append((T.SUMMARY, str(publication.pk)))
    for step in (T.MODERATION, T.EMBEDDING):
        if wanted(step):
            steps.append((step, str(publication.pk)))
    return steps


def launch(publication, types=None):
    """Planifie les analyses (toutes, ou seulement `types`) puis démarre le pipeline après commit."""
    from publications.models import Publication

    with transaction.atomic():
        for analysis_type, target_id in plan_steps(publication, types):
            AnalyseIA.objects.update_or_create(
                publication=publication,
                type=analysis_type,
                target_id=target_id,
                defaults={
                    "status": S.PENDING, "attempts": 0, "error": "", "result": {}, "score": None, "provider": "",
                    "model": "", "tokens": 0, "cached": False, "started_at": None, "finished_at": None,
                    "duration_ms": None,
                },
            )
        Publication.objects.filter(pk=publication.pk).update(status=Publication.Status.PENDING)
    publication.status = Publication.Status.PENDING
    publication_id = publication.pk
    transaction.on_commit(lambda: advance(publication_id))


def retry_failed(publication):
    """Relance les analyses en échec (ou bloquées en cours) d'une publication."""
    from publications.models import Publication

    reset = AnalyseIA.objects.filter(publication=publication, status__in=[S.FAILED, S.RUNNING]).exclude(
        type__in=AnalyseIA.SIDE_TYPES
    )
    count = reset.update(status=S.PENDING, attempts=0, error="", started_at=None, finished_at=None, duration_ms=None)
    if count:
        Publication.objects.filter(pk=publication.pk).update(status=Publication.Status.PENDING)
        publication_id = publication.pk
        transaction.on_commit(lambda: advance(publication_id))
    return count


def advance(publication_id):
    """Lance la prochaine analyse en file, ou calcule le statut final s'il n'y en a plus."""
    rows = list(AnalyseIA.objects.filter(publication_id=publication_id).exclude(type__in=AnalyseIA.SIDE_TYPES))
    if any(row.status in (S.RUNNING, S.RETRY) for row in rows):
        return None
    pending = [row for row in rows if row.status == S.PENDING]
    if pending:
        step = min(pending, key=lambda row: (ORDER.index(row.type), row.created_at))
        enqueue(import_string(STEP_TASKS[step.type]), [step.target_id])
        return step
    finalize(publication_id)
    return None


def finalize(publication_id):
    """Statut final : la modération prime, puis les échecs IA, sinon READY."""
    from publications.models import Publication
    from social.models import Notification
    from social.services import notify

    from .realtime import push_pipeline

    publication = Publication.objects.filter(pk=publication_id).select_related("author").first()
    if publication is None:
        return None
    rows = list(publication.analyses.exclude(type__in=AnalyseIA.SIDE_TYPES))
    if any(row.is_active for row in rows):
        return None

    moderation = next((row for row in rows if row.type == T.MODERATION and row.status == S.DONE), None)
    decision = (moderation.result or {}).get("decision") if moderation else None
    failed = [row for row in rows if row.status == S.FAILED]

    if decision == "BLOCK":
        status = Publication.Status.BLOCKED
    elif decision == "REVIEW":
        status = Publication.Status.UNDER_REVIEW
    elif failed:
        status = Publication.Status.FAILED
    else:
        status = Publication.Status.READY

    previous = publication.status
    Publication.objects.filter(pk=publication.pk).update(status=status)
    logger.info("Publication %s : %s → %s", publication.pk, previous, status)
    push_pipeline(publication.pk)

    if previous != Publication.Status.PENDING:
        return status
    messages = {
        Publication.Status.READY: (Notification.Type.AI_READY, "Votre publication est prête : description, transcription, tags et résumé ont été ajoutés."),
        Publication.Status.FAILED: (Notification.Type.AI_FAILED, "Une analyse IA de votre publication a échoué après 3 tentatives. Elle reste visible sous sa forme brute ; vous pouvez la relancer."),
        Publication.Status.UNDER_REVIEW: (Notification.Type.MODERATION, "Votre publication est en cours de vérification par un modérateur."),
        Publication.Status.BLOCKED: (Notification.Type.MODERATION, "Votre publication a été bloquée par la modération automatique. Elle reste visible par vous seul·e."),
    }
    notif_type, message = messages[status]
    notify(publication.author, notif_type, publication=publication, message=message)
    return status
