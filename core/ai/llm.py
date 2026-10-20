"""Routeur des fournisseurs d'IA : essaie Groq puis Claude (ordre réglable).

Chaque module appelle `llm.structured(...)` sans connaître le fournisseur ; si le
premier échoue (quota, panne, refus), le suivant prend le relais dans la même tentative.
"""

import logging
from dataclasses import dataclass

from django.conf import settings

from . import AIError, AIUnavailable, claude, groq_provider

logger = logging.getLogger("sensa.ai")

PROVIDERS = {"groq": groq_provider, "claude": claude}


@dataclass
class LLMResult:
    data: dict
    provider: str


def available():
    return [name for name in settings.SENSA_AI_PROVIDERS if name in PROVIDERS and PROVIDERS[name].is_configured()]


def is_configured():
    return bool(available())


def structured(prompt, schema, *, image=None, effort="low", max_tokens=2048):
    names = available()
    if not names:
        raise AIUnavailable("Aucun fournisseur d'IA n'est configuré (GROQ_API_KEY ou ANTHROPIC_API_KEY).")
    errors = []
    for name in names:
        try:
            if name == "claude":
                data = claude.structured(prompt, schema, image=image, effort=effort, max_tokens=max(max_tokens, 4096))
            else:
                data = groq_provider.structured(prompt, schema, image=image, max_tokens=max_tokens)
            if not isinstance(data, dict):
                raise AIError("Réponse JSON inattendue.")
            return LLMResult(data=data, provider=name)
        except AIError as exc:
            logger.warning("Fournisseur %s indisponible : %s", name, exc)
            errors.append(f"{name} : {exc}")
    if all("invalide" in error or "configurée" in error for error in errors):
        raise AIUnavailable(" ; ".join(errors))
    raise AIError(" ; ".join(errors))
