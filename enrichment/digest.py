"""Digest IA : synthèse des tendances de la communauté sur une période (module 3)."""

import logging
from collections import Counter

from django.db.models import Count, Q

from core.ai import AIError, llm

from .models import AnalyseIA, DigestIA, PublicationTag

logger = logging.getLogger("sensa.ai")

DIGEST_PROMPT = """Tu rédiges la lettre d'information de Sensa, un réseau social multimodal (texte, image, audio).
À partir des données de la période ci-dessous, rédige en français :
- un titre accrocheur (moins de 80 caractères) ;
- une synthèse de 3 ou 4 phrases sur ce que la communauté a partagé ;
- 3 à 5 temps forts, chacun avec un thème court et une phrase d'explication.
Reste fidèle aux données, sans inventer de chiffres ni de personnes.

<periode>{period}</periode>
<statistiques>{stats}</statistiques>
<tags_les_plus_utilises>{tags}</tags_les_plus_utilises>
<extraits_de_publications>
{excerpts}
</extraits_de_publications>"""

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"theme": {"type": "string"}, "description": {"type": "string"}},
                "required": ["theme", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "summary", "highlights"],
    "additionalProperties": False,
}


def collect(period_start, period_end):
    """Statistiques, tags dominants et extraits de la période (publications READY uniquement)."""
    from publications.models import Publication

    posts = (
        Publication.objects.filter(status=Publication.Status.READY, created_at__gte=period_start, created_at__lte=period_end)
        .select_related("audio")
        .prefetch_related("images")
        .order_by("-created_at")
    )
    post_list = list(posts[:200])
    audios = [post.audio_or_none for post in post_list if post.audio_or_none is not None]
    insights = AnalyseIA.objects.filter(
        publication__in=post_list, status=AnalyseIA.Status.DONE, type__in=[AnalyseIA.Type.TAGS, AnalyseIA.Type.SUMMARY]
    )
    categories, languages, summaries = Counter(), Counter(), {}
    for row in insights:
        result = row.result or {}
        if row.type == AnalyseIA.Type.TAGS:
            if result.get("category"):
                categories[result["category"]] += 1
            if result.get("language"):
                languages[result["language"]] += 1
        elif result.get("summary"):
            summaries[row.publication_id] = result["summary"]
    tags = (
        PublicationTag.objects.filter(publication__in=post_list)
        .values("tag__name")
        .annotate(n=Count("id"), ai=Count("id", filter=Q(source=PublicationTag.Source.AI)))
        .order_by("-n", "tag__name")[:10]
    )
    excerpts = []
    for post in post_list[:25]:
        text = summaries.get(post.pk) or (post.text or "").strip().replace("\n", " ")
        if text:
            excerpts.append(text[:220])
    stats = {
        "posts": len(post_list),
        "images": sum(1 for post in post_list if post.images.all()),
        "audios": len(audios),
        "audio_minutes": round(sum(audio.duration for audio in audios) / 60, 1),
        "categories": dict(categories.most_common(6)),
        "languages": dict(languages.most_common(4)),
    }
    top_tags = [{"name": row["tag__name"], "count": row["n"], "ai": row["ai"]} for row in tags]
    return stats, top_tags, excerpts


def _local_digest(digest, stats, top_tags):
    themes = list(stats["categories"])
    tag_names = ", ".join(f"#{tag['name']}" for tag in top_tags[:5]) or "aucun"
    summary = (
        f"{stats['posts']} publication(s) partagée(s), dont {stats['audios']} audio(s) "
        f"({stats['audio_minutes']} min transcrites) et {stats['images']} image(s) décrite(s). "
        f"Thèmes dominants : {', '.join(themes) if themes else 'variés'}. Tags les plus utilisés : {tag_names}."
    )
    highlights = [
        {"theme": theme.capitalize(), "description": f"{count} publication(s) sur ce thème."}
        for theme, count in list(stats["categories"].items())[:4]
    ]
    title = f"La semaine sur Sensa ({digest.period_start:%d/%m} – {digest.period_end:%d/%m})"
    return title, summary, highlights, "synthese-locale"


def generate(digest):
    """Calcule les statistiques et rédige le digest (API d'abord, synthèse locale en repli)."""
    try:
        stats, top_tags, excerpts = collect(digest.period_start, digest.period_end)
        digest.stats, digest.top_tags = stats, top_tags
        title = summary = None
        highlights, provider = [], ""
        if stats["posts"] and llm.is_configured():
            try:
                answer = llm.structured(
                    DIGEST_PROMPT.format(
                        period=f"du {digest.period_start:%d/%m/%Y} au {digest.period_end:%d/%m/%Y}",
                        stats=stats,
                        tags=", ".join(f"#{t['name']} ({t['count']})" for t in top_tags) or "aucun",
                        excerpts="\n".join(f"- {e}" for e in excerpts) or "—",
                    ),
                    DIGEST_SCHEMA,
                    max_tokens=2500,
                )
                title = (answer.data.get("title") or "").strip()[:160]
                summary = (answer.data.get("summary") or "").strip()
                highlights = [
                    {"theme": str(h.get("theme", ""))[:80], "description": str(h.get("description", ""))[:300]}
                    for h in answer.data.get("highlights", []) if isinstance(h, dict)
                ][:5]
                provider = answer.provider
            except AIError as exc:
                logger.warning("Digest par l'API indisponible : %s", exc)
        if not title or not summary:
            title, summary, highlights, provider = _local_digest(digest, stats, top_tags)
        digest.title, digest.summary, digest.highlights, digest.provider = title, summary, highlights, provider
        digest.status, digest.error = DigestIA.Status.DONE, ""
    except Exception as exc:  # le digest ne doit jamais rester « en préparation »
        logger.exception("Échec de la génération du digest %s", digest.pk)
        digest.status, digest.error = DigestIA.Status.FAILED, f"{type(exc).__name__}: {exc}"[:500]
    digest.save()
    return digest
