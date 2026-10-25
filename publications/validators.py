"""Validation des fichiers : type, 10 Mo et 50 mégapixels maximum, 3 minutes d'audio maximum (mesurées sur le fichier)."""

import os
import struct

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
            pixels = img.width * img.height
            img.verify()
    except Exception as exc:
        raise ValidationError("Ce fichier n'est pas une image valide.") from exc
    finally:
        upload.seek(0)
    if image_format not in IMAGE_FORMATS:
        raise ValidationError("Formats acceptés : JPEG, PNG ou WebP.")
    # Un petit fichier peut cacher une image immense (et saturer la mémoire une fois décompressée).
    if pixels > settings.SENSA_MAX_IMAGE_PIXELS:
        max_mp = settings.SENSA_MAX_IMAGE_PIXELS // 1_000_000
        raise ValidationError(f"L'image est trop grande : {max_mp} mégapixels maximum.")


def validate_audio_upload(upload):
    _check_size(upload, "L'audio")
    extension = os.path.splitext(upload.name or "")[1].lower()
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0]
    if extension not in AUDIO_EXTENSIONS and not content_type.startswith("audio/"):
        raise ValidationError("Formats audio acceptés : WebM, OGG, MP3, WAV ou M4A.")


def read_audio_duration(upload):
    """Durée réelle en secondes (mutagen, ou lecture du WebM), ou None si le format n'est pas lisible."""
    try:
        upload.seek(0)
        seconds = _read_mutagen_duration(upload)
        if seconds is None:
            upload.seek(0)
            seconds = read_webm_duration(upload.read())
        return seconds
    finally:
        upload.seek(0)


def _read_mutagen_duration(upload):
    try:
        import mutagen
    except ImportError:
        return None
    try:
        info = mutagen.File(upload)
        if info is not None and getattr(info, "info", None) is not None and info.info.length:
            return float(info.info.length)
    except Exception:
        return None
    return None


# Éléments EBML d'un fichier WebM (Matroska) utiles pour la durée.
WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
WEBM_CONTAINERS = {0x18538067, 0x1549A966, 0x1F43B675, 0xA0}  # Segment, Info, Cluster, BlockGroup
WEBM_TIMECODE_SCALE, WEBM_DURATION, WEBM_CLUSTER_TIMECODE = 0x2AD7B1, 0x4489, 0xE7
WEBM_BLOCKS = {0xA3, 0xA1}  # SimpleBlock, Block


def _read_vint(data, pos, keep_marker=False):
    """Entier de taille variable EBML : (valeur, position suivante, taille « inconnue »)."""
    first = data[pos]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or pos + length > len(data):
        raise ValueError("Entier EBML invalide.")
    value = first if keep_marker else first & (0xFF >> length)
    for byte in data[pos + 1: pos + length]:
        value = (value << 8) | byte
    unknown = not keep_marker and value == (1 << (7 * length)) - 1
    return value, pos + length, unknown


def read_webm_duration(data):
    """Durée en secondes d'un WebM, ou None.

    Les enregistrements de MediaRecorder (Chrome, Firefox) n'indiquent pas leur durée :
    on parcourt les blocs audio et on garde l'horodatage du dernier.
    """
    if not data.startswith(WEBM_MAGIC):
        return None
    scale = 1_000_000  # nanosecondes par unité de temps (valeur par défaut de Matroska)
    declared = last_block = None
    cluster_time = 0
    pos = 0
    try:
        while pos < len(data):
            element_id, pos, _ = _read_vint(data, pos, keep_marker=True)
            size, pos, unknown = _read_vint(data, pos)
            if element_id in WEBM_CONTAINERS:
                continue  # on lit ses éléments enfants à la suite
            if unknown or pos + size > len(data):
                break
            payload = data[pos: pos + size]
            pos += size
            if element_id == WEBM_TIMECODE_SCALE:
                scale = int.from_bytes(payload, "big")
            elif element_id == WEBM_DURATION and size in (4, 8):
                declared = struct.unpack(">f" if size == 4 else ">d", payload)[0]
            elif element_id == WEBM_CLUSTER_TIMECODE:
                cluster_time = int.from_bytes(payload, "big")
            elif element_id in WEBM_BLOCKS:
                _, offset, _ = _read_vint(payload, 0)  # numéro de piste
                relative = int.from_bytes(payload[offset: offset + 2], "big", signed=True)
                last_block = max(last_block or 0, cluster_time + relative)
    except (IndexError, ValueError):
        return None
    units = max(value for value in (declared, last_block, 0) if value is not None)
    return units * scale / 1e9 if units else None
