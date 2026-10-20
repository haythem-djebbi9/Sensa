"""Client Groq partagé : texte (gpt-oss), vision (Qwen) et transcription (Whisper).

- `structured(...)` renvoie un dict JSON (schéma imposé pour gpt-oss, mode JSON sinon) ;
- `transcribe(...)` renvoie texte, langue (code ISO), segments horodatés et durée.
"""

import base64
import json
import logging
import re
import threading
import time

from django.conf import settings

from . import AIError, AIUnavailable, usage

logger = logging.getLogger("sensa.ai")

_client = None
_lock = threading.Lock()

LANGUAGE_CODES = {
    "french": "fr", "english": "en", "arabic": "ar", "spanish": "es", "german": "de", "italian": "it",
    "portuguese": "pt", "dutch": "nl", "turkish": "tr", "russian": "ru", "chinese": "zh", "japanese": "ja",
}


def is_configured():
    return bool(settings.GROQ_API_KEY)


def get_client():
    global _client
    with _lock:
        if _client is None:
            from groq import Groq

            # max_retries : le SDK respecte retry-after en cas de limite de débit (429).
            _client = Groq(api_key=settings.GROQ_API_KEY, timeout=120.0, max_retries=3)
    return _client


def _wrap_errors(call):
    import groq

    try:
        return call()
    except groq.AuthenticationError as exc:
        raise AIUnavailable("Clé GROQ_API_KEY invalide.") from exc
    except groq.RateLimitError as exc:
        raise AIError("Limite de débit Groq atteinte.") from exc
    except groq.APIStatusError as exc:
        raise AIError(f"Erreur API Groq ({exc.status_code}).") from exc
    except groq.APIConnectionError as exc:
        raise AIError("Impossible de joindre l'API Groq.") from exc


def _extract_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise AIError("Réponse JSON introuvable.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIError("Réponse JSON invalide de Groq.") from exc


def structured(prompt, schema, *, image=None, max_tokens=2048, system=None):
    """Appel chat → dict JSON conforme à `schema`. `image` = (octets, type MIME)."""
    if not is_configured():
        raise AIUnavailable("GROQ_API_KEY n'est pas configurée.")
    model = settings.SENSA_GROQ_VISION_MODEL if image is not None else settings.SENSA_GROQ_TEXT_MODEL
    reasoning = model.startswith("openai/gpt-oss")
    if reasoning:
        content = prompt
    else:
        content = f"{prompt}\n\nRéponds uniquement avec un objet JSON conforme à ce schéma :\n{json.dumps(schema, ensure_ascii=False)}"
    if image is not None:
        data, media_type = image
        url = f"data:{media_type};base64,{base64.standard_b64encode(data).decode('ascii')}"
        content = [{"type": "text", "text": content}, {"type": "image_url", "image_url": {"url": url}}]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    kwargs = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
    if reasoning:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "sensa", "schema": schema}}
        kwargs["reasoning_effort"] = "low"
    elif image is None:
        kwargs["response_format"] = {"type": "json_object"}

    started = time.monotonic()
    response = _wrap_errors(lambda: get_client().chat.completions.create(**kwargs))
    latency = (time.monotonic() - started) * 1000
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise AIError("Réponse de Groq tronquée.")
    tokens = getattr(response.usage, "total_tokens", 0) if response.usage else 0
    usage.record("groq", model, tokens, latency)
    return _extract_json(choice.message.content)


def transcribe(filename, data, *, language=None, model=None):
    """Transcription Whisper (verbose_json) : texte, langue ISO, segments, durée."""
    if not is_configured():
        raise AIUnavailable("GROQ_API_KEY n'est pas configurée.")
    model = model or settings.SENSA_GROQ_WHISPER_MODEL
    kwargs = {"file": (filename, data), "model": model, "response_format": "verbose_json",
              "timestamp_granularities": ["segment"], "temperature": 0.0}
    if language:
        kwargs["language"] = language
    started = time.monotonic()
    response = _wrap_errors(lambda: get_client().audio.transcriptions.create(**kwargs))
    latency = (time.monotonic() - started) * 1000
    payload = response.to_dict() if hasattr(response, "to_dict") else response.model_dump()
    usage.record("groq", model, 0, latency)
    raw_language = (payload.get("language") or language or "").strip().lower()
    segments = [
        {"start": round(float(seg.get("start", 0)), 2), "end": round(float(seg.get("end", 0)), 2),
         "text": (seg.get("text") or "").strip()}
        for seg in payload.get("segments") or []
        if (seg.get("text") or "").strip()
    ]
    return {
        "text": (payload.get("text") or "").strip(),
        "language": LANGUAGE_CODES.get(raw_language, raw_language[:5]),
        "segments": segments,
        "duration": float(payload.get("duration") or 0),
        "model": model,
    }
