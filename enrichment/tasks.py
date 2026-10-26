"""Tâches Celery du module 3.

- transcribe_audio(audio_id)            : Whisper (Groq), langue, segments horodatés, cache par empreinte
- generate_tags(post_id)                : tags IA + confiance, thème principal, langue du texte
- summarize_post(post_id)               : résumé accessible (audio et textes longs)
- translate_transcription(audio_id, lang): traduction à la demande (fr / en / ar)
- pipeline_watchdog()                   : relance des analyses bloquées (Celery Beat)
- generate_digest(digest_id)            : synthèse IA des tendances d'une période
- weekly_digest()                       : digest de la semaine écoulée (Celery Beat, le lundi)
"""

from celery import shared_task

from . import ai, cache, vocabulary
from .models import AnalyseIA, PipelineConfig, PublicationTag, Tag
from .runner import StepResult, ensure_analysis, run_step


@shared_task(name="enrichment.transcribe_audio")
def transcribe_audio(audio_id):
    from publications.models import Audio

    audio = Audio.objects.select_related("publication").filter(pk=audio_id).first()
    if audio is None:
        return None
    analysis = ensure_analysis(audio.publication, AnalyseIA.Type.TRANSCRIPTION, audio.pk)
    config = PipelineConfig.get_solo()

    terms = vocabulary.active_terms()

    def save(output):
        # Corrige les noms propres mal transcrits, puis écrit dans le modèle Audio du module 1.
        text, segments, fixes = vocabulary.apply_corrections(output["text"], output.get("segments") or [], terms)
        audio.transcription = text
        audio.language = (output.get("language") or "")[:12]
        audio.segments = segments
        audio.save(update_fields=["transcription", "language", "segments"])
        return fixes

    def handler(analysis, last_attempt):
        key = cache.make_key(AnalyseIA.Type.TRANSCRIPTION, audio.file, config.whisper_model, config.language_hint)
        hit = cache.lookup(key)
        if hit is not None:
            fixes = save(hit.result)
            return StepResult(result=_transcription_summary(audio, fixes), provider="cache", model=hit.model, cached=True)
        output = ai.transcribe(
            audio, last_attempt=last_attempt, language=config.language_hint, model=config.whisper_model,
            prompt=vocabulary.whisper_prompt(terms),
        )
        cache.store(key, AnalyseIA.Type.TRANSCRIPTION,
                    {"text": output["text"], "language": output.get("language", ""), "segments": output.get("segments", [])},
                    output.get("provider", ""), output.get("model", ""))
        fixes = save(output)
        return StepResult(result=_transcription_summary(audio, fixes), provider=output.get("provider", ""),
                          model=output.get("model", ""))

    run_step(transcribe_audio, analysis, handler, task_args=[audio_id])
    return None


def _transcription_summary(audio, vocabulary_fixes=0):
    words = len(audio.transcription.split())
    return {
        # Version produite par l'IA : permet de revenir en arrière après une correction humaine.
        "text": audio.transcription,
        "segments": audio.segments,
        "vocabulary_fixes": vocabulary_fixes,
        "language": audio.language,
        "characters": len(audio.transcription),
        "words": words,
        "segment_count": len(audio.segments),
        "words_per_minute": round(words / (audio.duration / 60)) if audio.duration else None,
    }


@shared_task(name="enrichment.generate_tags")
def generate_tags(post_id):
    from publications.models import Publication

    publication = Publication.objects.prefetch_related("images").select_related("audio").filter(pk=post_id).first()
    if publication is None:
        return None
    analysis = ensure_analysis(publication, AnalyseIA.Type.TAGS, publication.pk)
    config = PipelineConfig.get_solo()

    def handler(analysis, last_attempt):
        description = _useful_descriptions(publication)
        audio = publication.audio_or_none
        output = ai.generate_tags(
            publication.text, description, audio.transcription if audio else "",
            last_attempt=last_attempt, max_tags=max(3, min(config.max_tags, 8)),
        )
        blocked = set(Tag.objects.filter(status=Tag.Status.BLOCKED).values_list("name", flat=True))
        output["author_tags"] = [name for name in output["author_tags"] if name not in blocked]
        output["ai_tags"] = [(name, c) for name, c in output["ai_tags"] if name not in blocked]
        PublicationTag.objects.filter(publication=publication).delete()
        for name in output["author_tags"]:
            tag, _ = Tag.objects.get_or_create(name=name)
            PublicationTag.objects.get_or_create(publication=publication, tag=tag, defaults={"source": PublicationTag.Source.AUTHOR})
        for name, confidence in output["ai_tags"]:
            tag, _ = Tag.objects.get_or_create(name=name)
            PublicationTag.objects.get_or_create(
                publication=publication, tag=tag, defaults={"source": PublicationTag.Source.AI, "confidence": confidence}
            )
        return StepResult(
            result={
                "tags": output["author_tags"] + [name for name, _ in output["ai_tags"]],
                "author_tags": output["author_tags"],
                "ai_tags": [{"name": name, "confidence": confidence} for name, confidence in output["ai_tags"]],
                "category": output["category"],
                "language": output["language"],
                "prompt": output.get("prompt", ""),
            },
            provider=output["provider"],
        )

    run_step(generate_tags, analysis, handler, task_args=[post_id])
    return None


def _useful_descriptions(publication):
    """Descriptions d'images utiles au sens (les descriptions « basiques » de couleurs sont ignorées)."""
    basic = set(
        AnalyseIA.objects.filter(
            publication=publication, type=AnalyseIA.Type.DESCRIPTION, provider="description-basique"
        ).values_list("target_id", flat=True)
    )
    return " ".join(
        image.description_ia for image in publication.images.all() if image.description_ia and str(image.pk) not in basic
    )


@shared_task(name="enrichment.summarize_post")
def summarize_post(post_id):
    from publications.models import Publication

    publication = Publication.objects.prefetch_related("images").select_related("audio").filter(pk=post_id).first()
    if publication is None:
        return None
    analysis = ensure_analysis(publication, AnalyseIA.Type.SUMMARY, publication.pk)

    def handler(analysis, last_attempt):
        audio = publication.audio_or_none
        output = ai.summarize(
            publication.text, _useful_descriptions(publication), audio.transcription if audio else "",
            last_attempt=last_attempt,
        )
        provider = output.pop("provider")
        return StepResult(result=output, provider=provider)

    run_step(summarize_post, analysis, handler, task_args=[post_id])
    return None


TRANSLATION_LANGUAGES = ("fr", "en", "ar")


@shared_task(name="enrichment.translate_transcription")
def translate_transcription(audio_id, language):
    from publications.models import Audio

    audio = Audio.objects.select_related("publication").filter(pk=audio_id).first()
    if audio is None or language not in TRANSLATION_LANGUAGES or not audio.transcription:
        return None
    analysis = ensure_analysis(audio.publication, AnalyseIA.Type.TRANSLATION, f"{audio.pk}:{language}")

    def handler(analysis, last_attempt):
        text, provider, version = ai.translate(audio.transcription, language)
        return StepResult(result={"language": language, "text": text, "prompt": version}, provider=provider)

    run_step(translate_transcription, analysis, handler, task_args=[audio_id, language])
    return None


@shared_task(name="enrichment.pipeline_watchdog")
def pipeline_watchdog():
    from .watchdog import requeue_stuck

    return requeue_stuck()


@shared_task(name="enrichment.generate_digest")
def generate_digest(digest_id):
    from .digest import generate

    from .models import DigestIA

    digest = DigestIA.objects.filter(pk=digest_id).first()
    if digest is not None:
        generate(digest)
    return None


@shared_task(name="enrichment.weekly_digest")
def weekly_digest():
    from datetime import timedelta

    from django.utils import timezone

    from .digest import generate
    from .models import DigestIA

    end = timezone.now()
    digest = DigestIA.objects.create(period_start=end - timedelta(days=7), period_end=end)
    generate(digest)
    return digest.pk
