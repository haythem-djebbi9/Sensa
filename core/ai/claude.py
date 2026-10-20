"""Client Claude (API Anthropic) partagé par les tâches IA des modules.

Utilisé pour la vision (module 1), le ton (module 2), les tags (module 3)
et la toxicité (module 4). Chaque appel renvoie un JSON validé par schéma.
"""

import base64
import json
import logging
import threading
import time

from django.conf import settings

from . import AIError, AIUnavailable, usage

logger = logging.getLogger("sensa.ai")

_client = None
_lock = threading.Lock()


def is_configured():
    return bool(settings.ANTHROPIC_API_KEY)


def get_client():
    global _client
    with _lock:
        if _client is None:
            import anthropic

            _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=90.0, max_retries=1)
    return _client


def structured(prompt, schema, *, system=None, image=None, effort="low", max_tokens=4096):
    """Envoie une requête à Claude et renvoie la réponse JSON (dict) conforme à `schema`.

    `image` est un tuple (octets, type MIME) pour les tâches de vision.
    Lève AIUnavailable si la clé est absente, AIError en cas d'échec.
    """
    if not is_configured():
        raise AIUnavailable("ANTHROPIC_API_KEY n'est pas configurée.")

    import anthropic

    content = []
    if image is not None:
        data, media_type = image
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": prompt})

    request = {
        "model": settings.SENSA_CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        # Repli côté serveur sur un autre modèle si la requête est refusée.
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
    if system:
        request["system"] = system

    started = time.monotonic()
    try:
        response = get_client().beta.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable("Clé ANTHROPIC_API_KEY invalide.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Quota de l'API Claude atteint.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Erreur API Claude ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Impossible de joindre l'API Claude.") from exc

    tokens_used = getattr(response, "usage", None)
    total = (getattr(tokens_used, "input_tokens", 0) or 0) + (getattr(tokens_used, "output_tokens", 0) or 0) if tokens_used else 0
    usage.record("claude", settings.SENSA_CLAUDE_MODEL, total, (time.monotonic() - started) * 1000)

    if response.stop_reason == "refusal":
        raise AIError("Claude a refusé de traiter ce contenu.")
    if response.stop_reason == "max_tokens":
        raise AIError("Réponse de Claude tronquée.")

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIError("Réponse JSON invalide de Claude.") from exc
