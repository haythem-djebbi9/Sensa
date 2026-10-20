"""Progression du pipeline poussée en temps réel à l'auteur (WebSocket Django Channels).

Après chaque étape, l'auteur reçoit {type: "pipeline", publication, status, done, total, step} :
la carte du post affiche « Étape 3/6 · Tags et thème… » puis se met à jour dès que c'est prêt.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import AnalyseIA

logger = logging.getLogger("sensa.ai")

STEP_ORDER = [
    AnalyseIA.Type.DESCRIPTION,
    AnalyseIA.Type.TRANSCRIPTION,
    AnalyseIA.Type.TAGS,
    AnalyseIA.Type.SUMMARY,
    AnalyseIA.Type.MODERATION,
    AnalyseIA.Type.EMBEDDING,
]


def progress(publication_id):
    """{done, total, percent, step} pour les étapes de pipeline d'une publication."""
    rows = list(
        AnalyseIA.objects.filter(publication_id=publication_id)
        .exclude(type__in=AnalyseIA.SIDE_TYPES)
        .only("type", "status", "created_at")
    )
    total = len(rows)
    done = sum(1 for row in rows if row.status in (AnalyseIA.Status.DONE, AnalyseIA.Status.FAILED))
    active = sorted(
        (row for row in rows if row.is_active),
        key=lambda row: (STEP_ORDER.index(row.type) if row.type in STEP_ORDER else 99, row.created_at),
    )
    step = active[0].get_type_display() if active else ""
    retry = any(row.status == AnalyseIA.Status.RETRY for row in rows)
    return {
        "done": done,
        "total": total,
        "percent": round(done / total * 100) if total else 100,
        "step": step,
        "retry": retry,
    }


def push_pipeline(publication_id):
    from publications.models import Publication
    from social.services import group_name

    publication = Publication.objects.filter(pk=publication_id).only("author_id", "status").first()
    layer = get_channel_layer()
    if publication is None or layer is None:
        return
    payload = {"type": "pipeline", "publication": str(publication_id), "status": publication.status}
    payload.update(progress(publication_id))
    try:
        async_to_sync(layer.group_send)(group_name(publication.author_id), {"type": "notify", "payload": payload})
    except Exception:  # le temps réel ne doit jamais casser le pipeline
        logger.debug("Progression du pipeline non envoyée", exc_info=True)
