"""Cache des analyses par empreinte de média : un même fichier n'est analysé qu'une fois.

L'empreinte combine le type d'analyse, le SHA-256 du fichier et les paramètres qui
changent le résultat (modèle Whisper, langue…). Republier la même image ou le même
vocal réutilise donc la description ou la transcription déjà payée.
"""

import hashlib

from django.db.models import F
from django.utils import timezone

from .models import CacheIA, PipelineConfig


def file_digest(file_field):
    digest = hashlib.sha256()
    with file_field.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_key(analysis_type, file_field, *params):
    raw = "|".join([analysis_type, file_digest(file_field), *[str(p) for p in params]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enabled():
    return PipelineConfig.get_solo().use_cache


def lookup(key):
    """Résultat en cache (et compteur de réutilisations incrémenté), ou None."""
    if not enabled():
        return None
    entry = CacheIA.objects.filter(key=key).first()
    if entry is None:
        return None
    CacheIA.objects.filter(pk=entry.pk).update(hits=F("hits") + 1, last_hit_at=timezone.now())
    return entry


def store(key, analysis_type, result, provider="", model=""):
    if not enabled():
        return None
    entry, _ = CacheIA.objects.update_or_create(
        key=key, defaults={"type": analysis_type, "result": result, "provider": provider[:40], "model": model[:80]}
    )
    return entry
