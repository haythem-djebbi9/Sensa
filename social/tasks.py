"""Tâche Celery du module 2 : analyse_tone(comment_id)."""

from celery import shared_task

from enrichment.models import AnalyseIA
from enrichment.runner import StepResult, ensure_analysis, run_step

from . import ai
from .models import Commentaire, Notification


@shared_task(name="social.analyse_tone")
def analyse_tone(comment_id):
    comment = Commentaire.objects.select_related("publication").filter(pk=comment_id).first()
    if comment is None:
        return None
    analysis = ensure_analysis(comment.publication, AnalyseIA.Type.TONE, comment.pk)

    def handler(analysis, last_attempt):
        tone, score, provider = ai.classify_tone(comment.text, last_attempt=last_attempt)
        comment.tone = tone
        comment.tone_score = score
        comment.save(update_fields=["tone", "tone_score"])
        # Priorise les notifications : les commentaires agressifs ou négatifs passent après.
        priority = {
            "positive": Notification.Priority.HIGH,
            "neutral": Notification.Priority.NORMAL,
            "negative": Notification.Priority.LOW,
            "aggressive": Notification.Priority.LOW,
        }[tone]
        Notification.objects.filter(commentaire=comment).update(priority=priority)
        return StepResult(result={"tone": tone}, score=score, provider=provider)

    run_step(analyse_tone, analysis, handler, task_args=[comment_id])
    return None
