"""Santé du système affichée dans les paramètres d'administration."""

import importlib.util
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from enrichment.models import AnalyseIA


def _has_module(name):
    return importlib.util.find_spec(name) is not None


def _celery_check():
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return "warn", "Mode local : tâches exécutées dans le serveur web (définissez REDIS_URL en production)"
    try:
        from sensa.celery import app

        replies = app.control.inspect(timeout=1.0).ping()
    except Exception as exc:  # broker injoignable
        return "down", f"Broker injoignable ({type(exc).__name__})"
    if not replies:
        return "down", "Aucun worker Celery ne répond"
    return "ok", f"{len(replies)} worker(s) actif(s)"


def _channels_check():
    backend = settings.CHANNEL_LAYERS["default"]["BACKEND"]
    if backend.endswith("InMemoryChannelLayer"):
        return "warn", "Couche en mémoire (un seul processus)"
    return "ok", "Redis"


def _ai_providers():
    from core.ai import groq_provider, llm

    whisper_local = _has_module("faster_whisper") or _has_module("whisper")
    chain = llm.available()
    names = {"groq": f"Groq ({settings.SENSA_GROQ_TEXT_MODEL})", "claude": f"Claude ({settings.SENSA_CLAUDE_MODEL})"}
    if groq_provider.is_configured():
        transcription = ("ok", f"Whisper Groq ({settings.SENSA_GROQ_WHISPER_MODEL})")
    elif settings.OPENAI_API_KEY:
        transcription = ("ok", "Whisper API OpenAI")
    elif whisper_local:
        transcription = ("ok", "Whisper local")
    else:
        transcription = ("down", "Aucun moteur (GROQ_API_KEY ou faster-whisper)")
    return [
        ("Texte : ton, tags, résumé, toxicité", "ok" if chain else "warn",
         " → ".join(names[name] for name in chain) if chain else "Replis locaux (aucune clé d'API)"),
        ("Vision (description d'image)", "ok" if chain else "warn",
         f"Groq ({settings.SENSA_GROQ_VISION_MODEL})" if "groq" in chain else names.get(chain[0], "") if chain else "Description basique locale"),
        ("Transcription audio", *transcription),
        ("Embeddings", "ok" if _has_module("sentence_transformers") else "warn",
         settings.SENSA_EMBEDDING_MODEL if _has_module("sentence_transformers") else "Vecteurs lexicaux locaux (hashing)"),
    ]


def system_health():
    since = timezone.now() - timedelta(hours=24)
    counts = dict(
        AnalyseIA.objects.filter(updated_at__gte=since).order_by().values("status").annotate(n=Count("id")).values_list("status", "n")
    )
    done = counts.get(AnalyseIA.Status.DONE, 0)
    failed = counts.get(AnalyseIA.Status.FAILED, 0)
    active = sum(counts.get(s, 0) for s in (AnalyseIA.Status.PENDING, AnalyseIA.Status.RUNNING, AnalyseIA.Status.RETRY))
    total = done + failed
    celery_state, celery_label = _celery_check()
    channels_state, channels_label = _channels_check()
    services = [
        ("Enrichissement IA (Celery)", celery_state, celery_label),
        ("Événements temps réel (Channels)", channels_state, channels_label),
        ("Stockage des médias", "ok", "MinIO / S3" if "s3" in settings.STORAGES["default"]["BACKEND"].lower() else "Disque local"),
    ]
    states = [state for _, state, _ in services]
    overall = "down" if "down" in states else "warn" if "warn" in states else "ok"
    return {
        "overall": overall,
        "services": services,
        "providers": _ai_providers(),
        "pipeline": {
            "done": done,
            "failed": failed,
            "active": active,
            "success_rate": round(done / total * 100) if total else None,
        },
        "checked_at": timezone.now(),
    }
