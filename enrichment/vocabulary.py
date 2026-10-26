"""Vocabulaire de transcription : contexte pour Whisper et corrections automatiques."""

import re

from .models import VocabulaireIA

MAX_PROMPT_CHARS = 600


def active_terms():
    return list(VocabulaireIA.objects.filter(is_active=True).order_by("category", "term"))


def whisper_prompt(terms=None):
    """Contexte transmis à Whisper : la liste des termes à bien orthographier."""
    terms = active_terms() if terms is None else terms
    words = []
    length = 0
    for entry in terms:
        if length + len(entry.term) + 2 > MAX_PROMPT_CHARS:
            break
        words.append(entry.term)
        length += len(entry.term) + 2
    return ", ".join(words)


def _patterns(terms):
    patterns = []
    for entry in terms:
        for variant in entry.variants_list:
            if variant.lower() != entry.term.lower():
                patterns.append((re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE), entry.term))
    return patterns


def apply_corrections(text, segments, terms=None):
    """Remplace les variantes mal transcrites. Renvoie (texte, segments, nombre de corrections)."""
    terms = active_terms() if terms is None else terms
    patterns = _patterns(terms)
    if not patterns:
        return text, segments, 0
    count = 0

    def fix(value):
        nonlocal count
        for pattern, term in patterns:
            value, n = pattern.subn(term, value)
            count += n
        return value

    text = fix(text or "")
    fixed_segments = [dict(seg, text=fix(seg.get("text", ""))) for seg in segments or []]
    return text, fixed_segments, count
