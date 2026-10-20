"""Validation des fichiers : type, 10 Mo maximum, 3 minutes d'audio maximum."""

import os

from django.conf import settings
from django.core.exceptions import ValidationError

IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
AUDIO_EXTENSIONS = {".webm", ".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".mp4", ".aac"}


def _check_size(upload, kind):
    if upload.size > settings.SENSA_MAX_UPLOAD_BYTES:
        max_mb = settings.SENSA_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValidationError(f"{kind} dépasse la taille maximale de {max_mb} Mo.")


def validate_image_upload(upload):
    _check_size(upload, "L'image")
    from PIL import Image as PILImage

    try:
        upload.seek(0)
        with PILImage.open(upload) as img:
            image_format = img.format
            img.verify()
    except Exception as exc:
        raise ValidationError("Ce fichier n'est pas une image valide.") from exc
    finally:
        upload.seek(0)
    if image_format not in IMAGE_FORMATS:
        raise ValidationError("Formats acceptés : JPEG, PNG ou WebP.")


def validate_audio_upload(upload):
    _check_size(upload, "L'audio")
    extension = os.path.splitext(upload.name or "")[1].lower()
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0]
    if extension not in AUDIO_EXTENSIONS and not content_type.startswith("audio/"):
        raise ValidationError("Formats audio acceptés : WebM, OGG, MP3, WAV ou M4A.")


def read_audio_duration(upload):
    """Durée réelle en secondes lue avec mutagen, ou None si le format n'est pas lisible."""
    try:
        import mutagen
    except ImportError:
        return None
    try:
        upload.seek(0)
        info = mutagen.File(upload)
        if info is not None and getattr(info, "info", None) is not None and info.info.length:
            return float(info.info.length)
    except Exception:
        return None
    finally:
        upload.seek(0)
    return None
