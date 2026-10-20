"""Tâche Celery du module 1 : describe_image(image_id)."""

from celery import shared_task

from enrichment import cache
from enrichment.models import AnalyseIA
from enrichment.runner import StepResult, ensure_analysis, run_step

from . import ai
from .models import Image


@shared_task(name="publications.describe_image")
def describe_image(image_id):
    image = Image.objects.select_related("publication").filter(pk=image_id).first()
    if image is None:
        return None
    analysis = ensure_analysis(image.publication, AnalyseIA.Type.DESCRIPTION, image.pk)

    def handler(analysis, last_attempt):
        # Une même image n'est décrite qu'une fois (cache du module 3).
        key = cache.make_key(AnalyseIA.Type.DESCRIPTION, image.file)
        hit = cache.lookup(key)
        if hit is not None:
            image.description_ia = hit.result.get("description", "")
            image.save(update_fields=["description_ia"])
            return StepResult(result=hit.result, provider="cache", model=hit.model, cached=True)
        description, provider = ai.describe_image(image, last_attempt=last_attempt)
        image.description_ia = description
        image.save(update_fields=["description_ia"])
        if provider not in ("description-basique",):
            cache.store(key, AnalyseIA.Type.DESCRIPTION, {"description": description}, provider)
        return StepResult(result={"description": description}, provider=provider)

    run_step(describe_image, analysis, handler, task_args=[image_id])
    return None
