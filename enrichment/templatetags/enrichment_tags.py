"""Balises de gabarit du module 3 : résumé, thème, langue, progression, traductions."""

from django import template

from ..models import AnalyseIA
from ..realtime import progress

register = template.Library()

LANGUAGES = {"fr": "Français", "en": "Anglais", "ar": "Arabe", "es": "Espagnol", "de": "Allemand", "it": "Italien"}
CATEGORIES = {
    "musique": "Musique", "nature": "Nature", "voyage": "Voyage", "cuisine": "Cuisine", "sport": "Sport",
    "art": "Art", "photographie": "Photographie", "technologie": "Technologie", "societe": "Société",
    "humour": "Humour", "quotidien": "Quotidien", "education": "Éducation", "sante": "Santé", "autre": "Autre",
}
SENTIMENTS = {"positive": "Positif", "neutral": "Neutre", "negative": "Négatif"}


@register.simple_tag
def ai_result(post, analysis_type):
    """Résultat d'une analyse terminée (préchargé par for_cards, sinon une requête)."""
    rows = getattr(post, "ai_insights", None)
    if rows is None:
        row = AnalyseIA.objects.filter(publication=post, type=analysis_type, status=AnalyseIA.Status.DONE).first()
        return row.result if row else {}
    for row in rows:
        if row.type == analysis_type:
            return row.result or {}
    return {}


@register.simple_tag
def pipeline_progress(post):
    return progress(post.pk)


@register.simple_tag
def translations(audio):
    """Traductions d'un audio : liste de {language, label, status, text, pk}."""
    rows = AnalyseIA.objects.filter(
        publication_id=audio.publication_id, type=AnalyseIA.Type.TRANSLATION, target_id__startswith=f"{audio.pk}:"
    ).order_by("created_at")
    result = []
    for row in rows:
        language = row.target_id.split(":", 1)[1]
        result.append({
            "language": language,
            "label": LANGUAGES.get(language, language),
            "status": row.status,
            "text": (row.result or {}).get("text", ""),
            "pk": row.pk,
            "error": row.error,
        })
    return result


@register.filter
def language_label(code):
    return LANGUAGES.get((code or "").lower(), code or "—")


@register.filter
def category_label(code):
    return CATEGORIES.get(code or "", code or "")


@register.filter
def sentiment_label(code):
    return SENTIMENTS.get(code or "", code or "")


@register.filter
def ms_label(value):
    if value is None or value == "":
        return "—"
    value = int(value)
    return f"{value} ms" if value < 1000 else f"{value / 1000:.1f} s".replace(".", ",")


@register.filter
def compact(value):
    """1284 → « 1,3 k »."""
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return value
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} M".replace(".", ",")
    if value >= 1000:
        return f"{value / 1000:.1f} k".replace(".", ",")
    return str(value)
