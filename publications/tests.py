from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from core.testing import SensaTestCase, image_upload, make_member, wav_upload
from enrichment.models import AnalyseIA

from .models import Publication


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
