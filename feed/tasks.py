"""Tâche Celery du module 5 : embed_post(post_id)."""

from celery import shared_task

from enrichment.models import AnalyseIA
from enrichment.runner import StepResult, ensure_analysis, run_step

from .ai import embed_text, publication_corpus


@shared_task(name="feed.embed_post")
def embed_post(post_id):
    from publications.models import Publication

    publication = (
        Publication.objects.prefetch_related("images", "tags").select_related("audio").filter(pk=post_id).first()
    )
    if publication is None:
        return None
    analysis = ensure_analysis(publication, AnalyseIA.Type.EMBEDDING, publication.pk)

    def handler(analysis, last_attempt):
        embedding = embed_text(publication_corpus(publication))
        Publication.objects.filter(pk=publication.pk).update(embedding=embedding)
        return StepResult(
            result={"model": embedding["model"], "dimensions": len(embedding["vector"])},
            provider=embedding["model"][:40],
        )

    run_step(embed_post, analysis, handler, task_args=[post_id])
    return None
