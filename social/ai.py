"""Tâche IA du module 2 : analyse du ton des commentaires.

Classes : positif, neutre, négatif, agressif. API (Groq, puis Claude) d'abord, lexique FR/EN en repli.
`quick_tone` (lexique seul, instantané) sert à l'avertissement « soyez bienveillant »
affiché avant la publication d'un commentaire agressif.
"""

import logging

from core.ai import AIError, AIUnavailable, llm
from core.ai.text import lexicon_sentiment, lexicon_toxicity

logger = logging.getLogger("sensa.ai")

TONE_PROMPT = """Classe le ton de ce commentaire publié sous un post de réseau social.
- positive : bienveillant, enthousiaste, reconnaissant ;
- neutral : informatif, question, remarque factuelle ;
- negative : critique, déçu, triste, sans attaque personnelle ;
- aggressive : insulte, moquerie ciblée, menace, mépris envers une personne.
Le score est ta confiance (0 à 1) dans la classe choisie.

<commentaire>{text}</commentaire>"""

TONE_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string", "enum": ["positive", "neutral", "negative", "aggressive"]},
        "score": {"type": "number"},
    },
    "required": ["tone", "score"],
    "additionalProperties": False,
}

AGGRESSIVE_THRESHOLD = 0.5


def quick_tone(text):
    """Classification locale immédiate : (ton, score)."""
    toxicity, _ = lexicon_toxicity(text)
    if toxicity >= AGGRESSIVE_THRESHOLD:
        return "aggressive", toxicity
    positive, negative = lexicon_sentiment(text)
    if positive > negative:
        return "positive", round(min(0.55 + 0.15 * (positive - negative), 0.95), 2)
    if negative > positive:
        return "negative", round(min(0.55 + 0.15 * (negative - positive), 0.95), 2)
    return "neutral", 0.6


def classify_tone(text, *, last_attempt=True):
    """Renvoie (ton, score, fournisseur)."""
    if llm.is_configured():
        try:
            result = llm.structured(TONE_PROMPT.format(text=text), TONE_SCHEMA, max_tokens=1024)
            tone = result.data.get("tone")
            if tone not in {"positive", "neutral", "negative", "aggressive"}:
                raise AIError(f"Ton inconnu : {tone}")
            return tone, round(min(max(float(result.data.get("score", 0.5)), 0.0), 1.0), 3), result.provider
        except (AIError, TypeError, ValueError) as exc:
            logger.warning("Analyse de ton par l'API indisponible : %s", exc)
            if not last_attempt and not isinstance(exc, AIUnavailable):
                raise
    tone, score = quick_tone(text)
    return tone, score, "lexique-local"
