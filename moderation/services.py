"""Tâche IA du module 4 : classification de toxicité.

`score_toxicity(text, image_description, transcription)` renvoie un score entre
0 et 1 (fonction appelée par le pipeline du module 3). L'API d'abord (Groq gpt-oss,
puis Claude), puis le modèle open source Detoxify s'il est installé, enfin un lexique FR/EN.
"""

import logging

from django.conf import settings

from core.ai import AIError, AIUnavailable, llm
from core.ai.text import lexicon_toxicity

logger = logging.getLogger("sensa.ai")

TOXICITY_PROMPT = """Tu es le système de modération d'un réseau social francophone.
Évalue la toxicité de la publication ci-dessous (texte, description de son image, transcription de son audio).

Score de 0 à 1 :
- 0.0 à 0.3 : contenu normal, y compris critique polie, humour, vulgarité légère non dirigée ;
- 0.5 à 0.8 : insultes, moqueries ciblées, propos désobligeants, contenu choquant ou douteux à vérifier ;
- 0.85 à 1 : harcèlement, menaces, appel à la violence, discours haineux visant un groupe, contenu sexuel explicite.

<texte>{text}</texte>
<description_image>{description}</description_image>
<transcription_audio>{transcription}</transcription_audio>"""

TOXICITY_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "labels": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["insulte", "harcelement", "menace", "haine", "sexuel", "violence", "spam", "autre"],
            },
        },
        "justification": {"type": "string"},
    },
    "required": ["score", "labels", "justification"],
    "additionalProperties": False,
}

_detoxify_model = None


def _detoxify(text):
    global _detoxify_model
    if not settings.SENSA_USE_LOCAL_MODELS:
        raise AIUnavailable("Modèles locaux désactivés.")
    try:
        from detoxify import Detoxify
    except ImportError as exc:
        raise AIUnavailable("detoxify n'est pas installé.") from exc
    if _detoxify_model is None:
        _detoxify_model = Detoxify("multilingual")
    scores = _detoxify_model.predict(text)
    labels = [name for name, value in scores.items() if name != "toxicity" and value >= 0.5]
    return float(scores.get("toxicity", 0.0)), labels


def analyse_toxicity(text, image_description="", transcription="", *, last_attempt=True):
    """Renvoie {score, labels, justification, provider}."""
    content = "\n".join(part for part in (text, image_description, transcription) if part).strip()
    if not content:
        return {"score": 0.0, "labels": [], "justification": "Aucun contenu textuel.", "provider": "aucun"}

    if llm.is_configured():
        try:
            result = llm.structured(
                TOXICITY_PROMPT.format(
                    text=text or "—", description=image_description or "—", transcription=transcription or "—"
                ),
                TOXICITY_SCHEMA,
                max_tokens=1536,
            )
            score = min(max(float(result.data.get("score", 0.0)), 0.0), 1.0)
            return {
                "score": round(score, 3),
                "labels": result.data.get("labels", []),
                "justification": result.data.get("justification", "")[:300],
                "provider": result.provider,
            }
        except (AIError, TypeError, ValueError) as exc:
            logger.warning("Modération par l'API indisponible : %s", exc)
            if not last_attempt and not isinstance(exc, AIUnavailable):
                raise

    lexicon_score, terms = lexicon_toxicity(text, image_description, transcription)
    try:
        model_score, labels = _detoxify(content)
        return {
            "score": round(max(model_score, lexicon_score), 3),
            "labels": labels,
            "justification": "Score du modèle Detoxify.",
            "provider": "detoxify-local",
        }
    except AIUnavailable:
        pass
    except Exception:
        logger.exception("Erreur du modèle Detoxify local")
    justification = f"Termes repérés : {', '.join(terms)}." if terms else "Aucun terme problématique repéré."
    return {"score": lexicon_score, "labels": ["insulte"] if terms else [], "justification": justification, "provider": "lexique-local"}


def score_toxicity(text, image_description="", transcription=""):
    """Score de toxicité entre 0 et 1 (interface demandée par le module 3)."""
    return analyse_toxicity(text, image_description, transcription)["score"]
