"""Tâche IA du module 1 : description d'image (vision).

Ordre des fournisseurs : Groq (Qwen vision) / Claude via le routeur → modèle open source local BLIP
(si installé) → description basique calculée localement (format, couleurs).
"""

import colorsys
import io
import logging

from django.conf import settings

from core.ai import AIError, llm

logger = logging.getLogger("sensa.ai")

DESCRIPTION_PROMPT = (
    "Tu rédiges le texte alternatif d'une image publiée sur un réseau social, pour une personne malvoyante.\n"
    "Décris l'image en français, en 1 à 3 phrases factuelles : sujet principal, action, lieu, ambiance.\n"
    "Si un texte est lisible dans l'image, cite-le. N'invente rien, ne commence pas par « Cette image »."
)
DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
    "additionalProperties": False,
}


def prepare_image(data, max_side=1568):
    """Réduit et ré-encode l'image en JPEG (< 5 Mo) pour l'envoyer à l'API."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    with PILImage.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue(), "image/jpeg"


def describe_with_llm(data):
    result = llm.structured(DESCRIPTION_PROMPT, DESCRIPTION_SCHEMA, image=prepare_image(data), max_tokens=1024)
    description = (result.data.get("description") or "").strip()
    if not description:
        raise AIError("Description vide.")
    return description, result.provider


_caption_pipeline = None


def describe_with_blip(data):
    """Repli open source : modèle de légende BLIP via transformers (optionnel)."""
    global _caption_pipeline
    if not settings.SENSA_USE_LOCAL_MODELS:
        raise AIError("Modèles locaux désactivés.")
    try:
        from PIL import Image as PILImage
        from PIL import ImageOps
        from transformers import pipeline
    except ImportError as exc:
        raise AIError("transformers n'est pas installé.") from exc
    if _caption_pipeline is None:
        _caption_pipeline = pipeline("image-to-text", model=settings.SENSA_CAPTION_MODEL)
    with PILImage.open(io.BytesIO(data)) as img:
        result = _caption_pipeline(ImageOps.exif_transpose(img).convert("RGB"))
    caption = (result[0].get("generated_text") or "").strip() if result else ""
    if not caption:
        raise AIError("Légende vide.")
    return f"{caption[0].upper()}{caption[1:]} (description générée en anglais)."


_COLOR_NAMES = [
    (0, "rouge"), (25, "orange"), (50, "jaune"), (80, "vert clair"), (140, "vert"),
    (185, "turquoise"), (215, "bleu"), (265, "violet"), (300, "rose"), (340, "rouge"),
]


def _color_name(rgb):
    r, g, b = (component / 255 for component in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.18:
        return "noir"
    if s < 0.15:
        return "blanc" if v > 0.85 else "gris"
    if s < 0.35 and v > 0.7 and 0.05 < h < 0.17:
        return "beige"
    if 0.03 < h < 0.11 and v < 0.6:
        return "brun"
    hue = h * 360
    name = "rouge"
    for start, label in _COLOR_NAMES:
        if hue >= start:
            name = label
    return name


def describe_basic(data):
    """Dernier repli : description factuelle calculée à partir des pixels."""
    from PIL import Image as PILImage
    from PIL import ImageOps

    with PILImage.open(io.BytesIO(data)) as img:
        # Les photos de téléphone sont souvent stockées couchées, avec l'orientation dans l'EXIF.
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        small = img.convert("RGB").resize((64, 64))
    quantized = small.quantize(colors=5)
    palette = quantized.getpalette() or []
    counts = sorted(quantized.getcolors() or [], reverse=True)
    names = []
    for _, index in counts:
        rgb = tuple(palette[index * 3: index * 3 + 3])
        if len(rgb) == 3:
            name = _color_name(rgb)
            if name not in names:
                names.append(name)
        if len(names) == 2:
            break
    luminance = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in small.getdata()) / (64 * 64 * 255)
    if width > height * 1.15:
        shape = "au format paysage"
    elif height > width * 1.15:
        shape = "au format portrait"
    else:
        shape = "de format carré"
    tones = " et ".join(names) if names else "variés"
    light = "lumineuse" if luminance > 0.6 else "sombre" if luminance < 0.3 else "aux contrastes équilibrés"
    return f"Image {shape} aux tons dominants {tones}, plutôt {light}."


def shorten(text, limit):
    """Raccourcit un texte à `limit` caractères en coupant après la dernière phrase complète,
    ou à défaut après le dernier mot complet."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(mark) for mark in (". ", "! ", "? ", ".»", ".\n"))
    if end >= limit // 2:
        return cut[: end + 1].strip()
    return cut[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def describe_image(image, *, last_attempt=True):
    """Décrit une image publiée (modèle Image) : renvoie (description en français, fournisseur utilisé)."""
    with image.file.open("rb") as handle:
        data = handle.read()
    return describe_image_data(data, last_attempt=last_attempt)


def describe_image_data(data, *, last_attempt=True):
    """Décrit une image à partir de ses octets : renvoie (description en français, fournisseur utilisé).

    Tant qu'il reste des tentatives, une erreur passagère de l'API est remontée
    pour que le pipeline relance plus tard ; à la dernière, on bascule en local.
    """
    if llm.is_configured():
        try:
            return describe_with_llm(data)
        except AIError as exc:
            logger.warning("Description par l'API indisponible : %s", exc)
            if not last_attempt:
                raise
    try:
        return describe_with_blip(data), "blip-local"
    except AIError:
        pass
    except Exception:  # modèle local défaillant
        logger.exception("Erreur du modèle BLIP local")
    return describe_basic(data), "description-basique"
