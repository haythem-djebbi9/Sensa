"""Laboratoire IA (module 3) : tester une tâche IA sur ses propres contenus.

Outil d'administration : contrairement au pipeline des publications (toujours dans
Celery), le test s'exécute pendant la requête pour afficher le résultat tout de suite.
"""

import os
import tempfile
import time
from types import SimpleNamespace

from django.core.files import File

from core.ai import AIError, usage

from . import ai, vocabulary

TASKS = [
    ("tags", "Tags, thème et langue"),
    ("summary", "Résumé"),
    ("translation", "Traduction"),
    ("tone", "Ton d'un commentaire"),
    ("toxicity", "Toxicité (modération)"),
    ("description", "Description d'image"),
    ("transcription", "Transcription audio"),
]
TEXT_TASKS = {"tags", "summary", "translation", "tone", "toxicity"}
PROMPT_TASKS = {"tags", "summary", "translation"}


class _TempMedia:
    """Fichier téléversé copié sur disque, présenté comme un Image/Audio du modèle (champ `file`)."""

    def __init__(self, upload, duration=0.0):
        suffix = os.path.splitext(upload.name)[1] or ".bin"
        handle, self.path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(handle, "wb") as out:
            for chunk in upload.chunks():
                out.write(chunk)
        django_file = File(open(self.path, "rb"), name=self.path)
        django_file.path = self.path
        self.media = SimpleNamespace(file=django_file, duration=duration, pk="laboratoire")

    def close(self):
        self.media.file.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def run(task, *, text="", image=None, audio=None, language="en", prompt=None):
    """Exécute la tâche et renvoie {result, provider, model, latency_ms, tokens, error}."""
    from moderation.services import analyse_toxicity
    from publications.ai import describe_image
    from social.ai import classify_tone

    started = time.monotonic()
    output = {"result": None, "provider": "", "model": "", "error": ""}
    media = None
    with usage.collect() as calls:
        try:
            if task == "tags":
                result = ai.generate_tags(text, prompt=prompt)
                output["provider"] = result.pop("provider")
                result["ai_tags"] = [{"name": n, "confidence": c} for n, c in result["ai_tags"]]
            elif task == "summary":
                result = ai.summarize(text, prompt=prompt)
                output["provider"] = result.pop("provider")
            elif task == "translation":
                translation, provider, version = ai.translate(text, language, prompt=prompt)
                result, output["provider"] = {"language": language, "translation": translation, "prompt": version}, provider
            elif task == "tone":
                tone, score, provider = classify_tone(text)
                result, output["provider"] = {"tone": tone, "score": score}, provider
            elif task == "toxicity":
                result = analyse_toxicity(text)
                output["provider"] = result.pop("provider")
            elif task == "description":
                media = _TempMedia(image)
                description, provider = describe_image(media.media)
                result, output["provider"] = {"description": description}, provider
            elif task == "transcription":
                media = _TempMedia(audio)
                terms = vocabulary.active_terms()
                raw = ai.transcribe(media.media, prompt=vocabulary.whisper_prompt(terms))
                text_fixed, segments, fixes = vocabulary.apply_corrections(raw["text"], raw.get("segments"), terms)
                result = {"text": text_fixed, "language": raw.get("language", ""), "segments": segments,
                          "vocabulary_fixes": fixes}
                output["provider"], output["model"] = raw.get("provider", ""), raw.get("model", "")
            else:
                raise ValueError("Tâche inconnue.")
            output["result"] = result
        except AIError as exc:
            output["error"] = str(exc)
        except Exception as exc:  # le laboratoire affiche l'erreur au lieu d'une page 500
            output["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if media is not None:
                media.close()
    if calls:
        output["provider"] = output["provider"] or calls[-1]["provider"]
        output["model"] = output["model"] or calls[-1]["model"]
    output["tokens"] = sum(call["tokens"] for call in calls)
    output["latency_ms"] = int((time.monotonic() - started) * 1000)
    output["calls"] = calls
    return output
