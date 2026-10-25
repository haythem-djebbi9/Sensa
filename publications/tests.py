import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from django.urls import reverse

from core.testing import SensaTestCase, image_upload, make_member, wav_upload
from enrichment.models import AnalyseIA

from . import ai
from .models import Publication, Republication


def phone_photo_upload():
    """Photo prise en portrait par un téléphone : pixels couchés (400×300), orientation EXIF 6,
    marque de l'appareil et position GPS."""
    from PIL import Image

    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010F] = "SensaPhone"
    exif[0x8825] = {1: "N", 2: (36.0, 52.0, 0.0)}
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 80, 40)).save(buffer, format="JPEG", exif=exif)
    return SimpleUploadedFile("portrait.jpg", buffer.getvalue(), content_type="image/jpeg")


def webm_upload(seconds, name="enregistrement.webm"):
    """WebM comme ceux de MediaRecorder : tailles « inconnues », pas de durée déclarée,
    un bloc Opus toutes les 20 ms, un cluster toutes les 10 secondes."""
    unknown = b"\x01\xff\xff\xff\xff\xff\xff\xff"

    def element(element_id, payload):
        return element_id + b"\x01" + len(payload).to_bytes(7, "big") + payload

    data = element(b"\x1a\x45\xdf\xa3", element(b"\x42\x82", b"webm"))
    data += b"\x18\x53\x80\x67" + unknown
    data += element(b"\x15\x49\xa9\x66", element(b"\x2a\xd7\xb1", (1_000_000).to_bytes(3, "big")))
    for cluster_ms in range(0, int(seconds * 1000), 10_000):
        data += b"\x1f\x43\xb6\x75" + unknown + element(b"\xe7", cluster_ms.to_bytes(4, "big"))
        for relative in range(0, min(10_000, int(seconds * 1000) - cluster_ms), 20):
            data += element(b"\xa3", b"\x81" + relative.to_bytes(2, "big", signed=True) + b"\x80\xfc\x00")
    return SimpleUploadedFile(name, data, content_type="audio/webm")


class PublishTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.client.force_login(self.lina)

    def test_text_post_published_then_enriched(self):
        response = self.publish(self.client, {"text": "Balade au bord de la mer à Bizerte #mer"})
        self.assertRedirects(response, reverse("feed:home"))
        post = Publication.objects.get()
        self.assertEqual(post.status, Publication.Status.READY)
        self.assertIn("mer", list(post.tags.values_list("name", flat=True)))
        self.assertIsNotNone(post.embedding)
        types = set(post.analyses.values_list("type", flat=True))
        self.assertEqual(types, {"tags", "moderation", "embedding"})

    def test_image_post_gets_ai_description(self):
        self.publish(self.client, {"text": ""}, {"image": image_upload()})
        post = Publication.objects.get()
        image = post.images.get()
        self.assertTrue(image.description_ia)
        self.assertEqual(post.status, Publication.Status.READY)
        description = post.analyses.get(type=AnalyseIA.Type.DESCRIPTION)
        self.assertEqual(description.status, AnalyseIA.Status.DONE)
        self.assertTrue(description.provider)

    def test_empty_post_rejected(self):
        response = self.publish(self.client, {"text": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Publication.objects.exists())

    def test_invalid_image_rejected(self):
        fake = SimpleUploadedFile("photo.png", b"pas une image", content_type="image/png")
        response = self.publish(self.client, {"text": "test"}, {"image": fake})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Publication.objects.exists())

    def test_gif_rejected(self):
        response = self.publish(self.client, {"text": "test"}, {"image": image_upload("anim.gif", image_format="GIF")})
        self.assertContains(response, "JPEG, PNG ou WebP")

    @override_settings(SENSA_MAX_UPLOAD_BYTES=100)
    def test_file_size_limit(self):
        response = self.publish(self.client, {"text": "test"}, {"image": image_upload(size=(200, 200))})
        self.assertContains(response, "taille maximale")

    @override_settings(SENSA_MAX_AUDIO_SECONDS=3)
    def test_audio_longer_than_limit_rejected(self):
        response = self.publish(self.client, {"text": "test"}, {"audio": wav_upload(seconds=5)})
        self.assertContains(response, "3 minutes")
        self.assertFalse(Publication.objects.exists())

    def test_audio_duration_read_from_file(self):
        with mock.patch("enrichment.ai.transcribe", return_value={"text": "Bonjour", "language": "fr", "segments": [], "provider": "test"}):
            self.publish(self.client, {"text": ""}, {"audio": wav_upload(seconds=2)})
        audio = Publication.objects.get().audio
        self.assertAlmostEqual(audio.duration, 2, delta=0.1)
        self.assertEqual(audio.transcription, "Bonjour")

    def test_browser_recording_duration_measured_on_server(self):
        with mock.patch("enrichment.ai.transcribe", return_value={"text": "Salut", "language": "fr", "segments": [], "provider": "test"}):
            self.publish(self.client, {"text": ""}, {"audio": webm_upload(seconds=5)})
        self.assertAlmostEqual(Publication.objects.get().audio.duration, 5, delta=0.1)

    def test_browser_reported_duration_not_trusted(self):
        # Le champ caché « audio_duration » ne doit plus permettre de contourner la limite.
        response = self.publish(self.client, {"text": "", "audio_duration": "10"}, {"audio": webm_upload(seconds=200)})
        self.assertContains(response, "3 minutes")
        self.assertFalse(Publication.objects.exists())

    def test_unreadable_audio_rejected(self):
        fake = SimpleUploadedFile("voix.webm", b"pas un audio", content_type="audio/webm")
        response = self.publish(self.client, {"text": "", "audio_duration": "10"}, {"audio": fake})
        self.assertContains(response, "Durée audio illisible")
        self.assertFalse(Publication.objects.exists())

    def test_image_metadata_removed_on_upload(self):
        from PIL import Image

        self.publish(self.client, {"text": "Vue depuis le balcon"}, {"image": phone_photo_upload()})
        stored = Publication.objects.get().images.get()
        with stored.file.open("rb") as handle:
            with Image.open(handle) as img:
                self.assertEqual(dict(img.getexif()), {})
                self.assertEqual(img.size, (300, 400))

    @override_settings(SENSA_POSTS_PER_HOUR=2)
    def test_rate_limit_returns_429(self):
        for index in range(2):
            self.publish(self.client, {"text": f"post {index}"})
        response = self.publish(self.client, {"text": "un de trop"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Publication.objects.count(), 2)


class VisibilityAndOwnershipTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.noah = make_member("noah")
        self.post = Publication.objects.create(author=self.lina, text="Bonjour", status=Publication.Status.READY)

    def test_under_review_and_blocked_hidden_from_others(self):
        self.client.force_login(self.noah)
        url = reverse("publications:detail", args=[self.post.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        for status in (Publication.Status.UNDER_REVIEW, Publication.Status.BLOCKED):
            Publication.objects.filter(pk=self.post.pk).update(status=status)
            self.assertEqual(self.client.get(url).status_code, 404)
        # L'auteur la voit toujours.
        self.client.force_login(self.lina)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_followers_only_visibility(self):
        from social.models import Abonnement

        Publication.objects.filter(pk=self.post.pk).update(visibility=Publication.Visibility.FOLLOWERS)
        self.assertFalse(self.post.is_visible_to(self.noah))
        Abonnement.objects.create(follower=self.noah, followed=self.lina)
        self.assertTrue(self.post.is_visible_to(self.noah))

    def test_only_author_can_edit_and_edit_relaunches_analysis(self):
        url = reverse("publications:edit", args=[self.post.pk])
        self.client.force_login(self.noah)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.lina)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"text": "Nouveau texte #modifie", "visibility": "PUBLIC"})
        self.assertRedirects(response, self.post.get_absolute_url())
        self.post.refresh_from_db()
        self.assertEqual(self.post.text, "Nouveau texte #modifie")
        self.assertEqual(self.post.status, Publication.Status.READY)
        self.assertIn("modifie", list(self.post.tags.values_list("name", flat=True)))

    def test_delete_by_author_or_moderator(self):
        from accounts.models import Membre

        url = reverse("publications:delete", args=[self.post.pk])
        self.client.force_login(self.noah)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(make_member("camille", role=Membre.Role.MODERATEUR))
        self.client.post(url)
        self.assertFalse(Publication.objects.exists())

    def test_card_partial_reports_status(self):
        self.client.force_login(self.lina)
        response = self.client.get(reverse("publications:card", args=[self.post.pk]))
        self.assertEqual(response["X-Post-Status"], "READY")
        self.assertContains(response, f'id="post-{self.post.pk}"')


class ImageAltTests(SensaTestCase):
    def setUp(self):
        post = Publication.objects.create(author=make_member("lina"), text="Photo")
        self.image = post.images.create(file=image_upload())

    def test_alt_default_while_description_pending(self):
        self.assertEqual(self.image.alt, "Image publiée sur Sensa (description en cours)")

    def test_alt_uses_ai_description(self):
        self.image.description_ia = "Un coucher de soleil sur la mer."
        self.assertEqual(self.image.alt, "Un coucher de soleil sur la mer.")

    def test_alt_prefers_author_text(self):
        self.image.description_ia = "Un coucher de soleil sur la mer."
        self.image.alt_text = "Ma plage préférée à Bizerte"
        self.assertEqual(self.image.alt, "Ma plage préférée à Bizerte")


class DescribeBasicTests(SensaTestCase):
    def test_respects_exif_orientation(self):
        post = Publication.objects.create(author=make_member("lina"), text="Photo")
        image = post.images.create(file=phone_photo_upload())
        self.assertIn("au format portrait", ai.describe_basic(image))


class MediaFileDeletionTests(SensaTestCase):
    def setUp(self):
        post = Publication.objects.create(author=make_member("lina"), text="Photo")
        self.image = post.images.create(file=image_upload())
        self.storage, self.name = self.image.file.storage, self.image.file.name

    def test_file_deleted_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.image.delete()
        self.assertFalse(self.storage.exists(self.name))

    def test_file_kept_when_deletion_rolled_back(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.image.delete()
                raise RuntimeError("annulation")
        self.assertTrue(self.storage.exists(self.name))


class RepublicationRulesTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.noah = make_member("noah")
        self.post = Publication.objects.create(author=self.lina, text="Bonjour", status=Publication.Status.READY)

    def test_member_can_republish_public_ready_post(self):
        self.assertTrue(self.post.can_republish(self.noah))

    def test_author_cannot_republish_own_post(self):
        self.assertFalse(self.post.can_republish(self.lina))

    def test_only_public_ready_posts_are_republishable(self):
        self.post.visibility = Publication.Visibility.FOLLOWERS
        self.assertFalse(self.post.can_republish(self.noah))
        self.post.visibility = Publication.Visibility.PUBLIC
        for status in (Publication.Status.PENDING, Publication.Status.UNDER_REVIEW, Publication.Status.BLOCKED):
            self.post.status = status
            self.assertFalse(self.post.can_republish(self.noah))

    def test_republication_is_unique_per_member(self):
        from django.db import IntegrityError

        Republication.objects.create(publication=self.post, member=self.noah)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Republication.objects.create(publication=self.post, member=self.noah)
