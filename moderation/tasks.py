"""Tâche Celery du module 4 : modération automatique d'une publication."""

from celery import shared_task

from enrichment.models import AnalyseIA
from enrichment.runner import StepResult, ensure_analysis, run_step

from .models import ConfigurationIA
from .services import analyse_toxicity


@shared_task(name="moderation.moderate_publication")
def moderate_publication(post_id):
    from publications.models import Publication

    publication = Publication.objects.prefetch_related("images").select_related("audio").filter(pk=post_id).first()
    if publication is None:
        return None
    analysis = ensure_analysis(publication, AnalyseIA.Type.MODERATION, publication.pk)

    def handler(analysis, last_attempt):
        description = " ".join(image.description_ia for image in publication.images.all() if image.description_ia)
        audio = publication.audio_or_none
        verdict = analyse_toxicity(
            publication.text, description, audio.transcription if audio else "", last_attempt=last_attempt
        )
        config = ConfigurationIA.get_solo()
        decision = config.decide(verdict["score"])
        return StepResult(
            result={
                "decision": decision,
                "labels": verdict["labels"],
                "justification": verdict["justification"],
                "thresholds": {"review": config.threshold_review, "block": config.threshold_block},
            },
            score=verdict["score"],
            provider=verdict["provider"],
        )

    run_step(moderate_publication, analysis, handler, task_args=[post_id])
    return None
