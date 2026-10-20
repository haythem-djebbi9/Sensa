"""Outils partagés par les tests des modules."""

import io
import math
import wave

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import Membre


def make_member(username, role=Membre.Role.MEMBRE, **extra):
    return Membre.objects.create_user(
        username=username, email=f"{username}@test.local", password="MotDePasse-Test-123", role=role, **extra
    )


def image_upload(name="photo.png", size=(64, 48), color=(91, 61, 245), image_format="PNG"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}[image_format]
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


def wav_upload(seconds=2, name="voix.wav", rate=4000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(rate)
        wav.writeframes(bytes(int(128 + 60 * math.sin(i / 3)) for i in range(int(seconds * rate))))
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="audio/wav")


class SensaTestCase(TestCase):
    """TestCase qui exécute les callbacks on_commit (lancement du pipeline IA)."""

    def publish(self, client, data, files=None):
        payload = dict(data)
        payload.setdefault("visibility", "PUBLIC")
        if files:
            payload.update(files)
        with self.captureOnCommitCallbacks(execute=True):
            return client.post("/publications/nouvelle/", payload)

