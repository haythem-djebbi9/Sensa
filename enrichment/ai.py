"""Tâches IA du module 3 : transcription (Whisper), tags + thème + langue, résumé, traduction.

Fournisseurs :
- transcription : Whisper sur Groq → Whisper API OpenAI → Whisper local (faster-whisper / openai-whisper) ;
- texte (tags, résumé, traduction) : routeur `core.ai.llm` (Groq gpt-oss, puis Claude) → repli local.
"""

import logging
import os
from difflib import SequenceMatcher
import re
import tempfile
import threading

from django.conf import settings

from core.ai import AIError, AIUnavailable, groq_provider, llm
from core.ai.text import extract_hashtags, guess_language, keywords, slug_tag

logger = logging.getLogger("sensa.ai")

CATEGORIES = [
    "musique", "nature", "voyage", "cuisine", "sport", "art", "photographie", "technologie",
    "societe", "humour", "quotidien", "education", "sante", "autre",
]
LANGUAGE_NAMES = {"fr": "français", "en": "anglais", "ar": "arabe", "es": "espagnol", "de": "allemand", "it": "italien"}


# --- Transcription -------------------------------------------------------------

def _groq_whisper(audio, language="", model=""):
    with audio.file.open("rb") as handle:
        data = handle.read()
    filename = os.path.basename(audio.file.name) or "audio.webm"
    output = groq_provider.transcribe(filename, data, language=language or None, model=model or None)
    output["provider"] = "groq-whisper"
    return output


def _openai_whisper(audio, language=""):
    """Whisper via l'API OpenAI (format verbose_json pour la langue et les segments)."""
    try:
        import openai
    except ImportError as exc:
        raise AIUnavailable("Le paquet openai n'est pas installé.") from exc
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY, timeout=120.0, max_retries=1)
    with audio.file.open("rb") as handle:
        data = handle.read()
    filename = os.path.basename(audio.file.name) or "audio.webm"
    kwargs = {"model": settings.SENSA_WHISPER_API_MODEL, "file": (filename, data), "response_format": "verbose_json"}
    if language:
        kwargs["language"] = language
    try:
        response = client.audio.transcriptions.create(**kwargs)
    except openai.AuthenticationError as exc:
        raise AIUnavailable("Clé OPENAI_API_KEY invalide.") from exc
    except openai.OpenAIError as exc:
        raise AIError(f"Erreur de l'API Whisper : {exc}") from exc
    segments = [
        {"start": float(getattr(seg, "start", 0) or 0), "end": float(getattr(seg, "end", 0) or 0), "text": getattr(seg, "text", "")}
        for seg in (getattr(response, "segments", None) or [])
    ]
    return {
        "text": (response.text or "").strip(),
        "language": groq_provider.LANGUAGE_CODES.get((getattr(response, "language", "") or "").lower(), getattr(response, "language", "") or ""),
        "segments": segments,
        "provider": "openai-whisper",
        "model": settings.SENSA_WHISPER_API_MODEL,
    }


_local_model = None
_local_lock = threading.Lock()


def _local_audio_path(audio):
    """Chemin local du fichier (copie temporaire si le stockage est distant, ex. MinIO)."""
    try:
        return audio.file.path, False
    except NotImplementedError:
        suffix = os.path.splitext(audio.file.name)[1] or ".webm"
        with audio.file.open("rb") as handle, tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(handle.read())
        return tmp.name, True


def _whisper_local(audio, language=""):
    """Repli open source : faster-whisper, sinon openai-whisper (nécessite ffmpeg)."""
    global _local_model
    if not settings.SENSA_USE_LOCAL_MODELS:
        raise AIUnavailable("Modèles locaux désactivés.")
    path, temporary = _local_audio_path(audio)
    try:
        try:
            from faster_whisper import WhisperModel

            with _local_lock:
                if _local_model is None:
                    _local_model = WhisperModel(settings.SENSA_WHISPER_LOCAL_MODEL, device="cpu", compute_type="int8")
            segments, info = _local_model.transcribe(path, language=language or None)
            segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
            detected = info.language
            provider = "faster-whisper-local"
        except ImportError:
            try:
                import whisper
            except ImportError as exc:
                raise AIUnavailable(
                    "Aucun moteur de transcription : définissez GROQ_API_KEY ou installez faster-whisper."
                ) from exc
            with _local_lock:
                if _local_model is None:
                    _local_model = whisper.load_model(settings.SENSA_WHISPER_LOCAL_MODEL)
            result = _local_model.transcribe(path, language=language or None)
            segments = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in result.get("segments", [])]
            detected = result.get("language", "")
            provider = "whisper-local"
    finally:
        if temporary:
            os.unlink(path)
    text = " ".join(seg["text"].strip() for seg in segments).strip()
    return {"text": text, "language": detected or "", "segments": segments, "provider": provider,
            "model": settings.SENSA_WHISPER_LOCAL_MODEL}


def transcribe(audio, *, last_attempt=True, language="", model=""):
    """Transcrit un Audio : Whisper Groq, puis API OpenAI, puis Whisper local."""
    remote = []
    if groq_provider.is_configured():
        remote.append(lambda: _groq_whisper(audio, language, model))
    if settings.OPENAI_API_KEY:
        remote.append(lambda: _openai_whisper(audio, language))
    last_error = None
    for attempt in remote:
        try:
            return attempt()
        except AIError as exc:
            logger.warning("Transcription distante indisponible : %s", exc)
            last_error = exc
    if last_error is not None and not last_attempt and not isinstance(last_error, AIUnavailable):
        raise last_error
    return _whisper_local(audio, language)


# --- Tags, thème et langue ------------------------------------------------------

TAGS_PROMPT = """Analyse cette publication de réseau social pour la rendre plus facile à découvrir.
1. Propose de 3 à {max_tags} hashtags : un ou deux mots collés, en minuscules, sans « # », sans accents
   ni espaces, de préférence en français, fidèles au contenu (pas de tags génériques comme « photo » ou « post »,
   pas de variantes d'un même mot, pas de collage de deux autres tags).
   Pour chacun, donne ta confiance entre 0 et 1.
2. Choisis le thème principal parmi : {categories}.
3. Donne la langue principale du texte (code ISO 639-1, ex. « fr », « en », « ar »).

<texte>{text}</texte>
<description_image>{description}</description_image>
<transcription_audio>{transcription}</transcription_audio>"""

TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "confidence": {"type": "number"}},
                "required": ["name", "confidence"],
                "additionalProperties": False,
            },
        },
        "category": {"type": "string", "enum": CATEGORIES},
        "language": {"type": "string"},
    },
    "required": ["tags", "category", "language"],
    "additionalProperties": False,
}


def generate_tags(text, description="", transcription="", *, last_attempt=True, max_tags=5):
    """Renvoie {author_tags, ai_tags: [(nom, confiance)], category, language, provider}."""
    author_tags = extract_hashtags(text)
    corpus = "\n".join(part for part in (text, description, transcription) if part).strip()
    result = {"author_tags": author_tags, "ai_tags": [], "category": "", "language": guess_language(corpus), "provider": "auteur"}
    if not corpus:
        return result

    if llm.is_configured():
        try:
            answer = llm.structured(
                TAGS_PROMPT.format(
                    max_tags=max_tags, categories=", ".join(CATEGORIES), text=text or "—",
                    description=description or "—", transcription=transcription or "—",
                ),
                TAGS_SCHEMA,
                max_tokens=1536,
            )
            ai_tags = []
            for item in answer.data.get("tags", []):
                name = slug_tag(item.get("name", "") if isinstance(item, dict) else str(item))
                confidence = float(item.get("confidence", 0.5)) if isinstance(item, dict) else 0.5
                if not is_redundant(name, author_tags + [t for t, _ in ai_tags]):
                    ai_tags.append((name, round(min(max(confidence, 0.0), 1.0), 2)))
            category = answer.data.get("category", "")
            language = (answer.data.get("language") or "").lower()[:5]
            result.update(
                ai_tags=ai_tags[:max_tags],
                category=category if category in CATEGORIES else "autre",
                language=language or result["language"],
                provider=answer.provider,
            )
            return result
        except (AIError, TypeError, ValueError) as exc:
            logger.warning("Tags par l'API indisponibles : %s", exc)
            if not last_attempt and not isinstance(exc, AIUnavailable):
                raise

    result["ai_tags"] = [(word, None) for word in keywords(corpus, limit=max_tags) if word not in author_tags]
    result["provider"] = "mots-cles-local"
    return result


def is_redundant(name, existing):
    """Tag vide, doublon, variante orthographique (« accesibilite ») ou collage de deux tags (« musiquenature »)."""
    if not name or len(name) < 2:
        return True
    for other in existing:
        if name == other or SequenceMatcher(None, name, other).ratio() >= 0.88:
            return True
    return any(name == a + b for a in existing for b in existing if a != b)


# --- Résumé ---------------------------------------------------------------------

SUMMARY_PROMPT = """Résume cette publication en français, en 1 ou 2 phrases (240 caractères maximum),
pour une personne qui ne peut pas écouter l'audio ni voir l'image. Reste fidèle au contenu.
Donne aussi 3 mots-clés et le sentiment général (positive, neutral ou negative).

<texte>{text}</texte>
<description_image>{description}</description_image>
<transcription_audio>{transcription}</transcription_audio>"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
    },
    "required": ["summary", "keywords", "sentiment"],
    "additionalProperties": False,
}


def _first_sentences(text, limit=240):
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    summary = ""
    for sentence in sentences:
        if len(summary) + len(sentence) > limit:
            break
        summary = f"{summary} {sentence}".strip()
    return summary or (text or "")[:limit].rstrip() + ("…" if len(text or "") > limit else "")


def summarize(text, description="", transcription="", *, last_attempt=True):
    """Renvoie {summary, keywords, sentiment, provider}."""
    if llm.is_configured():
        try:
            answer = llm.structured(
                SUMMARY_PROMPT.format(text=text or "—", description=description or "—", transcription=transcription or "—"),
                SUMMARY_SCHEMA,
                max_tokens=1536,
            )
            summary = (answer.data.get("summary") or "").strip()
            if not summary:
                raise AIError("Résumé vide.")
            return {
                "summary": summary[:400],
                "keywords": [str(k) for k in answer.data.get("keywords", [])][:5],
                "sentiment": answer.data.get("sentiment", "neutral"),
                "provider": answer.provider,
            }
        except (AIError, TypeError, ValueError) as exc:
            logger.warning("Résumé par l'API indisponible : %s", exc)
            if not last_attempt and not isinstance(exc, AIUnavailable):
                raise
    source = transcription or text
    return {
        "summary": _first_sentences(source),
        "keywords": keywords(" ".join(part for part in (text, transcription) if part), limit=3),
        "sentiment": "neutral",
        "provider": "extrait-local",
    }


# --- Traduction ------------------------------------------------------------------

TRANSLATION_PROMPT = """Traduis fidèlement cette transcription en {language}. Conserve le sens et le ton,
sans rien ajouter. Réponds avec la traduction complète.

<transcription>{text}</transcription>"""

TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
    "additionalProperties": False,
}


def translate(text, target_language):
    """Traduction par l'API (pas de repli local : sans fournisseur, l'analyse échoue)."""
    language = LANGUAGE_NAMES.get(target_language, target_language)
    answer = llm.structured(TRANSLATION_PROMPT.format(language=language, text=text), TRANSLATION_SCHEMA, max_tokens=3000)
    translation = (answer.data.get("translation") or "").strip()
    if not translation:
        raise AIError("Traduction vide.")
    return translation, answer.provider
