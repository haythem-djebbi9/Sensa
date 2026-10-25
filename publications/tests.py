import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from django.urls import reverse

from core.testing import SensaTestCase, image_upload, make_member, wav_upload
from enrichment.models import AnalyseIA

from . import ai
from .models import Enregistrement, Publication, Republication


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

    def test_carousel_of_three_images(self):
        colors = [(200, 40, 40), (40, 200, 40), (40, 40, 200)]
        files = [image_upload(f"photo{index}.png", color=color) for index, color in enumerate(colors)]
        self.publish(self.client, {"text": "Ma journée en 3 photos", "alt_text": "Le port au matin"}, {"image": files})
        post = Publication.objects.get()
        images = list(post.images.all())
        self.assertEqual(len(images), 3)
        self.assertEqual([image.alt_text for image in images], ["Le port au matin", "", ""])
        self.assertTrue(all(image.description_ia for image in images))
        self.assertEqual(post.analyses.filter(type=AnalyseIA.Type.DESCRIPTION).count(), 3)
        response = self.client.get(reverse("publications:card", args=[post.pk]))
        self.assertContains(response, "data-carousel")
        self.assertContains(response, "1/3")

    @override_settings(SENSA_MAX_IMAGES=2)
    def test_too_many_images_rejected(self):
        files = [image_upload(f"photo{index}.png") for index in range(3)]
        response = self.publish(self.client, {"text": "test"}, {"image": files})
        self.assertContains(response, "2 images maximum par publication.")
        self.assertFalse(Publication.objects.exists())

    def test_single_image_has_no_carousel(self):
        self.publish(self.client, {"text": ""}, {"image": image_upload()})
        response = self.client.get(reverse("publications:card", args=[Publication.objects.get().pk]))
        self.assertNotContains(response, "data-carousel")

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

    @override_settings(SENSA_MAX_IMAGE_PIXELS=1_000_000)
    def test_image_pixel_limit(self):
        # Fichier PNG de quelques Ko, mais un peu plus d'un mégapixel une fois décompressé.
        response = self.publish(self.client, {"text": "test"}, {"image": image_upload(size=(1001, 1000))})
        self.assertContains(response, "L&#x27;image est trop grande : 1 mégapixels maximum.")
        self.assertFalse(Publication.objects.exists())

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
        self.assertIn("au format portrait", ai.describe_image(image)[0])


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


class RepublishToggleTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.noah = make_member("noah")
        self.post = Publication.objects.create(author=self.lina, text="Bonjour", status=Publication.Status.READY)
        self.url = reverse("publications:republish_toggle", args=[self.post.pk])
        self.client.force_login(self.noah)

    def test_republish_then_undo(self):
        response = self.client.post(self.url, headers={"X-Requested-With": "fetch"})
        self.assertEqual(response.json(), {"republished": True, "count": 1})
        response = self.client.post(self.url, headers={"X-Requested-With": "fetch"})
        self.assertEqual(response.json(), {"republished": False, "count": 0})
        self.assertFalse(Republication.objects.exists())

    def test_author_notified_once(self):
        from social.models import Notification

        for _ in range(3):
            self.client.post(self.url)
        notifications = Notification.objects.filter(recipient=self.lina, type=Notification.Type.REPUBLISH)
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().title, "noah a republié votre publication")

    def test_own_post_refused(self):
        self.client.force_login(self.lina)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertFalse(Republication.objects.exists())

    def test_followers_only_post_refused(self):
        from social.models import Abonnement

        Abonnement.objects.create(follower=self.noah, followed=self.lina)
        Publication.objects.filter(pk=self.post.pk).update(visibility=Publication.Visibility.FOLLOWERS)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_card_shows_button_and_count(self):
        Republication.objects.create(publication=self.post, member=self.noah)
        response = self.client.get(reverse("publications:card", args=[self.post.pk]))
        self.assertContains(response, "data-repost-form")
        self.assertContains(response, 'class="action-btn republished"')
        self.client.force_login(self.lina)
        response = self.client.get(reverse("publications:card", args=[self.post.pk]))
        self.assertNotContains(response, "data-repost-form")
        self.assertContains(response, 'title="Republications"')


class RepublicationProfileTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.noah = make_member("noah")
        self.first = Publication.objects.create(author=self.lina, text="Premier", status=Publication.Status.READY)
        self.second = Publication.objects.create(author=self.lina, text="Second", status=Publication.Status.READY)
        from datetime import timedelta

        from django.utils import timezone

        Republication.objects.create(publication=self.second, member=self.noah)
        newest = Republication.objects.create(publication=self.first, member=self.noah)
        Republication.objects.filter(pk=newest.pk).update(created_at=timezone.now() + timedelta(minutes=1))
        self.client.force_login(self.lina)
        self.url = reverse("accounts:profile", args=["noah"])

    def test_tab_lists_reposts_newest_first(self):
        response = self.client.get(self.url, {"onglet": "republications", "vue": "liste"})
        self.assertEqual(list(response.context["posts"]), [self.first, self.second])
        self.assertEqual(response.context["republications_count"], 2)
        self.assertEqual(response.context["posts_count"], 0)

    def test_counts_are_not_limited_to_the_member(self):
        Republication.objects.create(publication=self.first, member=make_member("maya"))
        response = self.client.get(self.url, {"onglet": "republications"})
        self.assertEqual({post.pk: post.republish_count for post in response.context["posts"]}[self.first.pk], 2)

    def test_hidden_original_not_listed(self):
        Publication.objects.filter(pk=self.first.pk).update(status=Publication.Status.BLOCKED)
        self.client.force_login(make_member("maya"))
        response = self.client.get(self.url, {"onglet": "republications"})
        self.assertEqual(list(response.context["posts"]), [self.second])

    def test_default_tab_shows_own_posts(self):
        response = self.client.get(reverse("accounts:profile", args=["lina"]))
        self.assertEqual(response.context["tab"], "publications")
        self.assertEqual(response.context["posts_count"], 2)


class EditKeepsModerationTests(SensaTestCase):
    def setUp(self):
        from accounts.models import Membre

        self.lina = make_member("lina")
        self.camille = make_member("camille", role=Membre.Role.MODERATEUR)
        self.client.force_login(self.lina)
        self.publish(self.client, {"text": "Un post tout à fait normal"})
        self.post = Publication.objects.get()
        self.edit_url = reverse("publications:edit", args=[self.post.pk])

    def decide(self, action):
        self.client.force_login(self.camille)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("moderation:decide", args=[self.post.pk]), {"action": action})
        self.client.force_login(self.lina)

    def edit(self, execute=True):
        with self.captureOnCommitCallbacks(execute=execute):
            self.client.post(self.edit_url, {"text": "Un post modifié", "visibility": "PUBLIC"})
        self.post.refresh_from_db()

    def test_moderator_block_survives_edit(self):
        self.decide("block")
        self.edit()
        self.assertEqual(self.post.status, Publication.Status.BLOCKED)

    def test_blocked_post_stays_hidden_during_reanalysis(self):
        self.decide("block")
        self.edit(execute=False)
        self.assertEqual(self.post.status, Publication.Status.BLOCKED)

    def test_edit_after_approval_is_ready(self):
        self.decide("block")
        self.decide("approve")
        self.edit()
        self.assertEqual(self.post.status, Publication.Status.READY)

    def test_reported_post_stays_under_review_after_edit(self):
        for name in ("noah", "maya", "karim"):
            self.client.force_login(make_member(name))
            self.client.post(reverse("moderation:report", args=[self.post.pk]), {"reason": "SPAM", "details": ""})
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Publication.Status.UNDER_REVIEW)
        self.client.force_login(self.lina)
        self.edit()
        self.assertEqual(self.post.status, Publication.Status.UNDER_REVIEW)


class SavedPostsTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.noah = make_member("noah")
        self.post = Publication.objects.create(author=self.lina, text="Recette du couscous", status=Publication.Status.READY)
        self.url = reverse("publications:save_toggle", args=[self.post.pk])
        self.client.force_login(self.noah)

    def test_save_then_unsave(self):
        self.assertEqual(self.client.post(self.url, headers={"X-Requested-With": "fetch"}).json(), {"saved": True})
        self.assertEqual(self.client.post(self.url, headers={"X-Requested-With": "fetch"}).json(), {"saved": False})
        self.assertFalse(Enregistrement.objects.exists())

    def test_own_post_can_be_saved(self):
        self.client.force_login(self.lina)
        self.client.post(self.url)
        self.assertTrue(Enregistrement.objects.filter(member=self.lina).exists())

    def test_saved_tab_is_private(self):
        Enregistrement.objects.create(publication=self.post, member=self.noah)
        response = self.client.get(reverse("accounts:profile", args=["noah"]), {"onglet": "enregistres"})
        self.assertEqual(list(response.context["posts"]), [self.post])
        self.assertContains(response, "Enregistrés")
        self.client.force_login(self.lina)
        response = self.client.get(reverse("accounts:profile", args=["noah"]), {"onglet": "enregistres"})
        self.assertEqual(response.context["tab"], "publications")
        self.assertNotContains(response, "Enregistrés")

    def test_hidden_post_leaves_saved_tab(self):
        Enregistrement.objects.create(publication=self.post, member=self.noah)
        Publication.objects.filter(pk=self.post.pk).update(status=Publication.Status.BLOCKED)
        response = self.client.get(reverse("accounts:profile", args=["noah"]), {"onglet": "enregistres"})
        self.assertEqual(list(response.context["posts"]), [])

    def test_card_shows_saved_state(self):
        Enregistrement.objects.create(publication=self.post, member=self.noah)
        response = self.client.get(reverse("publications:card", args=[self.post.pk]))
        self.assertContains(response, 'class="action-btn action-end saved"')


TRANSCRIPTION = {"text": "Bonjour", "language": "fr", "segments": [], "provider": "test"}


class EditMediaTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.client.force_login(self.lina)

    def create(self, text="Ma publication", **files):
        with mock.patch("enrichment.ai.transcribe", return_value=TRANSCRIPTION):
            self.publish(self.client, {"text": text}, files)
        return Publication.objects.get()

    def edit(self, post, data, files=None):
        payload = {"text": post.text, "visibility": "PUBLIC", **data, **(files or {})}
        with mock.patch("enrichment.ai.transcribe", return_value=TRANSCRIPTION), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("publications:edit", args=[post.pk]), payload)
        post.refresh_from_db()
        return response

    def test_edit_page_lists_media(self):
        post = self.create(image=[image_upload(), image_upload()], audio=wav_upload())
        response = self.client.get(reverse("publications:edit", args=[post.pk]))
        self.assertContains(response, 'name="remove_images"', count=2)
        self.assertContains(response, 'name="remove_audio"')

    def test_add_image_to_text_post(self):
        post = self.create()
        self.edit(post, {}, {"new_images": [image_upload()]})
        image = post.images.get()
        self.assertTrue(image.description_ia)
        self.assertEqual(post.status, Publication.Status.READY)

    def test_remove_one_image(self):
        post = self.create(image=[image_upload("a.png"), image_upload("b.png")])
        first, second = post.images.all()
        storage, name = first.file.storage, first.file.name
        self.edit(post, {"remove_images": [first.pk]})
        self.assertEqual(list(post.images.all()), [second])
        self.assertFalse(storage.exists(name))
        targets = set(post.analyses.filter(type=AnalyseIA.Type.DESCRIPTION).values_list("target_id", flat=True))
        self.assertEqual(targets, {str(second.pk)})

    def test_replace_audio(self):
        post = self.create(audio=wav_upload(seconds=1))
        old = post.audio
        self.edit(post, {}, {"audio": wav_upload(seconds=2)})
        post = Publication.objects.get(pk=post.pk)
        self.assertNotEqual(post.audio.pk, old.pk)
        self.assertAlmostEqual(post.audio.duration, 2, delta=0.1)
        targets = set(post.analyses.filter(type=AnalyseIA.Type.TRANSCRIPTION).values_list("target_id", flat=True))
        self.assertEqual(targets, {str(post.audio.pk)})

    def test_remove_audio_drops_its_summary(self):
        post = self.create(audio=wav_upload())
        self.assertTrue(post.analyses.filter(type=AnalyseIA.Type.SUMMARY).exists())
        self.edit(post, {"remove_audio": "on"})
        post = Publication.objects.get(pk=post.pk)
        self.assertIsNone(post.audio_or_none)
        self.assertFalse(post.analyses.filter(type__in=[AnalyseIA.Type.SUMMARY, AnalyseIA.Type.TRANSCRIPTION]).exists())

    def test_cannot_remove_everything_without_text(self):
        post = self.create(text="", image=[image_upload()])
        response = self.edit(post, {"text": "", "remove_images": [post.images.get().pk]})
        self.assertContains(response, "Une publication sans média doit contenir du texte.")
        self.assertEqual(post.images.count(), 1)

    @override_settings(SENSA_MAX_IMAGES=2)
    def test_image_limit_counts_existing_images(self):
        post = self.create(image=[image_upload(), image_upload()])
        response = self.edit(post, {}, {"new_images": [image_upload()]})
        self.assertContains(response, "2 images maximum par publication.")
        self.assertEqual(post.images.count(), 2)

    def test_alt_text_of_first_image_still_editable(self):
        post = self.create(image=[image_upload()])
        self.edit(post, {"alt_text": "Le port de Bizerte"})
        self.assertEqual(post.images.get().alt_text, "Le port de Bizerte")


class AltTextSuggestionTests(SensaTestCase):
    url = "/publications/suggerer-texte-alternatif/"

    def setUp(self):
        self.lina = make_member("lina")
        self.client.force_login(self.lina)

    def suggest(self, upload=None):
        return self.client.post(self.url, {"image": upload or image_upload()}, headers={"X-Requested-With": "fetch"})

    def test_suggestion_from_vision_model(self):
        with mock.patch("publications.ai.llm.is_configured", return_value=True), \
                mock.patch("publications.ai.describe_with_llm", return_value=("Une plage au coucher du soleil.", "groq")):
            response = self.suggest()
        self.assertEqual(response.json(), {"alt_text": "Une plage au coucher du soleil.", "approximate": False})

    def test_basic_description_flagged_as_approximate(self):
        with mock.patch("publications.ai.llm.is_configured", return_value=False):
            data = self.suggest().json()
        self.assertTrue(data["approximate"])
        self.assertTrue(data["alt_text"].startswith("Image "))

    def test_suggestion_reused_when_publishing(self):
        upload = image_upload("plage.png", color=(30, 120, 220))
        with mock.patch("publications.ai.llm.is_configured", return_value=True), \
                mock.patch("publications.ai.describe_with_llm", return_value=("Une plage.", "groq")) as describe:
            self.suggest(upload)
            upload.seek(0)
            self.publish(self.client, {"text": "", "alt_text": "Une plage."}, {"image": upload})
        self.assertEqual(describe.call_count, 1)
        analysis = Publication.objects.get().analyses.get(type=AnalyseIA.Type.DESCRIPTION)
        self.assertTrue(analysis.cached)

    def test_invalid_image_rejected(self):
        fake = SimpleUploadedFile("photo.png", b"pas une image", content_type="image/png")
        response = self.suggest(fake)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Ce fichier n'est pas une image valide.")

    @override_settings(SENSA_ALT_SUGGESTIONS_PER_HOUR=2)
    def test_rate_limited(self):
        with mock.patch("publications.ai.llm.is_configured", return_value=False):
            statuses = [self.suggest().status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])

    def test_login_and_post_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)

    def test_long_suggestion_cut_after_last_full_sentence(self):
        long_text = "Un diagramme de cas d'utilisation. " + "Un acteur relié à plusieurs actions du système " * 8
        with mock.patch("publications.ai.llm.is_configured", return_value=True), \
                mock.patch("publications.ai.describe_with_llm", return_value=(long_text, "groq")):
            alt_text = self.suggest().json()["alt_text"]
        self.assertLessEqual(len(alt_text), 300)
        self.assertTrue(alt_text.endswith("…") or alt_text.endswith("."))
        self.assertNotIn("  ", alt_text)


class ShortenTests(SensaTestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(ai.shorten("Une plage.", 300), "Une plage.")

    def test_cut_after_last_sentence(self):
        text = "Première phrase assez longue pour compter. Deuxième phrase. Troisième phrase coupée ici"
        self.assertEqual(ai.shorten(text, 70), "Première phrase assez longue pour compter. Deuxième phrase.")

    def test_cut_after_last_word_without_sentence(self):
        self.assertEqual(ai.shorten("un deux trois quatre cinq six", 16), "un deux trois…")
