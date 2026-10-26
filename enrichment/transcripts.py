"""Correction humaine des transcriptions (avec historique et retour à la version IA)."""

from django.db import transaction

from . import pipeline
from .models import AnalyseIA, CorrectionTranscription

#: Étapes qui dépendent du texte de la transcription : relancées après une correction.
DEPENDENT_STEPS = [AnalyseIA.Type.TAGS, AnalyseIA.Type.SUMMARY, AnalyseIA.Type.MODERATION, AnalyseIA.Type.EMBEDDING]


def ai_original(audio):
    """(texte, segments) produits par l'IA, avant toute correction humaine."""
    first = audio.corrections.order_by("created_at").first()
    if first is not None:
        return first.text_before, first.segments_before
    analysis = AnalyseIA.objects.filter(
        publication_id=audio.publication_id, type=AnalyseIA.Type.TRANSCRIPTION, target_id=str(audio.pk)
    ).first()
    if analysis and (analysis.result or {}).get("text"):
        return analysis.result["text"], analysis.result.get("segments", [])
    return audio.transcription, audio.segments


def is_corrected(audio):
    latest = audio.corrections.order_by("-created_at").first()
    return latest is not None and not latest.is_revert


@transaction.atomic
def save_correction(audio, user, *, text, segments, note="", is_revert=False):
    """Enregistre la nouvelle transcription, l'historique, puis relance les analyses dépendantes."""
    correction = CorrectionTranscription.objects.create(
        audio=audio,
        author=user,
        text_before=audio.transcription,
        segments_before=audio.segments,
        text_after=text,
        segments_after=segments,
        note=note[:200],
        is_revert=is_revert,
    )
    audio.transcription = text
    audio.segments = segments
    audio.save(update_fields=["transcription", "segments"])
    pipeline.launch(audio.publication, types=DEPENDENT_STEPS)
    return correction


def edit_segments(audio, new_texts):
    """Applique les textes saisis aux segments existants (les horodatages ne changent pas)."""
    if audio.segments:
        segments = [dict(seg, text=new.strip()) for seg, new in zip(audio.segments, new_texts)]
        segments = [seg for seg in segments if seg["text"]]
        text = " ".join(seg["text"] for seg in segments)
    else:
        text = " ".join(t.strip() for t in new_texts if t.strip())
        segments = []
    return text, segments
