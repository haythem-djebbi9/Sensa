"""Tests des écrans de gestion du module 3 : tags, vocabulaire, prompts, laboratoire, digests, corrections."""

from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from accounts.models import Membre
from core.ai import AIError
from core.testing import SensaTestCase, image_upload, make_member, wav_upload
from publications.models import Audio, Publication

from . import prompts, vocabulary
from .digest import generate
from .models import AnalyseIA, CorrectionTranscription, DigestIA, PromptIA, PublicationTag, Tag, VocabulaireIA
from .views_gestion import merge_tags, probable_duplicates

T = AnalyseIA.Type


def fake_transcription(text="On se retrouve à ain drahem puis à bizerte."):
    return {"text": text, "language": "fr", "provider": "test", "model": "whisper-test",
            "segments": [{"start": 0.0, "end": 3.0, "text": text}]}


class ModeratorClientMixin:
    def setUp(self):
        self.moderator = make_member("camille", role=Membre.Role.MODERATEUR)
        self.admin = make_member("avery", role=Membre.Role.ADMIN)
        self.member = make_member("lina")
        self.client.force_login(self.moderator)


class TagCrudTests(ModeratorClientMixin, SensaTestCase):
    def link(self, name, source=PublicationTag.Source.AI, author=None):
        post = Publication.objects.create(author=author or self.member, text="post", status=Publication.Status.READY)
        tag, _ = Tag.objects.get_or_create(name=name)
        PublicationTag.objects.create(publication=post, tag=tag, source=source, confidence=0.8)
        return post, tag

    def test_create_update_delete(self):
        response = self.client.post(reverse("enrichment:tag_create"), {"name": "#Randonnée Été", "description": "", "status": "active"})
        tag = Tag.objects.get()
        self.assertEqual(tag.name, "randonneeete")
        self.assertRedirects(response, reverse("enrichment:tag_detail", args=[tag.pk]))
        self.client.post(reverse("enrichment:tag_update", args=[tag.pk]), {"name": "randonnee", "description": "Sorties", "status": "blocked"})
        tag.refresh_from_db()
        self.assertEqual((tag.name, tag.status), ("randonnee", Tag.Status.BLOCKED))
        self.assertEqual(self.client.get(reverse("enrichment:tag_delete", args=[tag.pk])).status_code, 200)
        self.client.post(reverse("enrichment:tag_delete", args=[tag.pk]))
        self.assertFalse(Tag.objects.exists())

    def test_rename_to_existing_name_is_refused(self):
        Tag.objects.create(name="mer")
        other = Tag.objects.create(name="ocean")
        response = self.client.post(reverse("enrichment:tag_update", args=[other.pk]), {"name": "mer", "description": "", "status": "active"})
        self.assertContains(response, "existe déjà")

    def test_merge_moves_links_without_duplicates(self):
        post_a, source = self.link("accesibilite")
        _, target = self.link("accessibilite")
        PublicationTag.objects.create(publication=post_a, tag=target, source=PublicationTag.Source.AUTHOR)
        post_b = Publication.objects.create(author=self.member, text="autre", status=Publication.Status.READY)
        PublicationTag.objects.create(publication=post_b, tag=source)
        moved = merge_tags(source, target)
        self.assertEqual(moved, 1)
        self.assertFalse(Tag.objects.filter(name="accesibilite").exists())
        self.assertEqual(target.links.count(), 3)

    def test_merge_view_and_duplicates(self):
        self.link("accesibilite")
        _, keep = self.link("accessibilite")
        self.link("accessibilite")
        pairs = probable_duplicates()
        self.assertEqual((pairs[0]["drop"].name, pairs[0]["keep"].name), ("accesibilite", "accessibilite"))
        drop = Tag.objects.get(name="accesibilite")
        self.client.post(reverse("enrichment:tag_merge", args=[drop.pk]), {"target": keep.pk})
        self.assertEqual(list(Tag.objects.values_list("name", flat=True)), ["accessibilite"])

    def test_list_filters_and_permissions(self):
        self.link("mer")
        Tag.objects.create(name="spam", status=Tag.Status.BLOCKED)
        response = self.client.get(reverse("enrichment:tags"), {"statut": "blocked"})
        self.assertEqual([tag.name for tag in response.context["page"]], ["spam"])
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("enrichment:tags")).status_code, 403)

    def test_blocked_tags_are_never_added(self):
        Tag.objects.create(name="cuisine", status=Tag.Status.BLOCKED)
        self.client.force_login(self.member)
        self.publish(self.client, {"text": "Couscous au poisson préparé avec patience #cuisine"})
        post = Publication.objects.get(text__startswith="Couscous")
        names = set(post.tags.values_list("name", flat=True))
        self.assertNotIn("cuisine", names)
        self.assertTrue(names)


class VocabularyTests(ModeratorClientMixin, SensaTestCase):
    def test_crud_and_toggle(self):
        self.client.post(reverse("enrichment:vocabulary_create"),
                         {"term": "Aïn  Draham", "variants": "ain drahem, aïn drahem, ain drahem", "category": "lieu", "is_active": "on"})
        entry = VocabulaireIA.objects.get()
        self.assertEqual((entry.term, entry.variants), ("Aïn Draham", "ain drahem, aïn drahem"))
        self.assertEqual(entry.created_by, self.moderator)
        self.client.post(reverse("enrichment:vocabulary_toggle", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)
        self.client.post(reverse("enrichment:vocabulary_update", args=[entry.pk]),
                         {"term": "Aïn Draham", "variants": "ain draham", "category": "lieu", "is_active": "on"})
        entry.refresh_from_db()
        self.assertTrue(entry.is_active)
        self.client.post(reverse("enrichment:vocabulary_delete", args=[entry.pk]))
        self.assertFalse(VocabulaireIA.objects.exists())

    def test_whisper_prompt_and_corrections(self):
        VocabulaireIA.objects.create(term="Aïn Draham", variants="ain drahem")
        VocabulaireIA.objects.create(term="Bizerte", variants="bizert", is_active=False)
        self.assertEqual(vocabulary.whisper_prompt(), "Aïn Draham")
        text, segments, count = vocabulary.apply_corrections(
            "Direction Ain Drahem !", [{"start": 0, "text": "Direction ain drahem"}]
        )
        self.assertEqual((text, segments[0]["text"], count), ("Direction Aïn Draham !", "Direction Aïn Draham", 2))

    def test_vocabulary_used_by_transcription(self):
        VocabulaireIA.objects.create(term="Aïn Draham", variants="ain drahem")
        self.client.force_login(self.member)
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()) as transcribe:
            self.publish(self.client, {"text": ""}, {"audio": wav_upload()})
        self.assertEqual(transcribe.call_args.kwargs["prompt"], "Aïn Draham")
        audio = Audio.objects.get()
        self.assertIn("Aïn Draham", audio.transcription)
        analysis = AnalyseIA.objects.get(type=T.TRANSCRIPTION)
        self.assertEqual(analysis.result["vocabulary_fixes"], 2)  # texte + segment


class PromptTests(ModeratorClientMixin, SensaTestCase):
    def test_admin_only(self):
        self.assertEqual(self.client.get(reverse("enrichment:prompts")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("enrichment:prompts")), "Prompts IA versionnés")

    def test_validation_of_variables(self):
        with self.assertRaises(Exception):
            prompts.validate(PromptIA.Task.TRANSLATION, "Traduis {text}")  # {language} manquant
        with self.assertRaises(Exception):
            prompts.validate(PromptIA.Task.SUMMARY, "Résume {text} pour {public}")  # variable inconnue
        with self.assertRaises(Exception):
            prompts.validate(PromptIA.Task.TAGS, "Tags pour {text")  # accolade mal formée
        prompts.validate(PromptIA.Task.TAGS, "Tags {{json}} pour {text}")

    def test_versions_activation_and_usage(self):
        self.client.force_login(self.admin)
        url = reverse("enrichment:prompt_create")
        response = self.client.post(url, {"task": "summary", "name": "Court", "template": "Résume : {text}", "notes": "", "activate_now": "on"})
        self.assertRedirects(response, reverse("enrichment:prompts"))
        self.client.post(url, {"task": "summary", "name": "Long", "template": "Résume longuement : {text} {transcription}", "notes": ""})
        first, second = PromptIA.objects.order_by("version")
        self.assertEqual((first.version, second.version, first.is_active, second.is_active), (1, 2, True, False))
        self.assertEqual(prompts.render(PromptIA.Task.SUMMARY, text="X")[0], "Résume : X")
        self.client.post(reverse("enrichment:prompt_activate", args=[second.pk]))
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertEqual(prompts.resolve(PromptIA.Task.SUMMARY)[1], "v2")
        self.client.post(reverse("enrichment:prompt_default", args=["summary"]))
        self.assertEqual(prompts.resolve(PromptIA.Task.SUMMARY)[1], "défaut")
        # Le prompt actif est réellement envoyé à l'IA.
        prompts.activate(first)
        with mock.patch("core.ai.llm.is_configured", return_value=True), \
                mock.patch("core.ai.llm.structured") as structured:
            structured.return_value = mock.Mock(data={"summary": "Ok.", "keywords": [], "sentiment": "neutral"}, provider="groq")
            from enrichment.ai import summarize

            result = summarize("Bonjour")
        self.assertEqual(structured.call_args.args[0], "Résume : Bonjour")
        self.assertEqual(result["prompt"], "v1")

    def test_invalid_template_rejected_by_form(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("enrichment:prompt_create"),
                                    {"task": "translation", "name": "x", "template": "Traduis {text}", "notes": ""})
        self.assertContains(response, "Variables obligatoires manquantes")
        self.assertFalse(PromptIA.objects.exists())


class LaboratoryTests(ModeratorClientMixin, SensaTestCase):
    def test_text_tasks_run_with_local_fallbacks(self):
        url = reverse("enrichment:laboratory")
        response = self.client.post(url, {"task": "tags", "text": "Balade au port de Bizerte avec les mouettes #mer", "language": "en"})
        output = response.context["output"]
        self.assertEqual(output["error"], "")
        self.assertIn("mer", output["result"]["author_tags"])
        response = self.client.post(url, {"task": "toxicity", "text": "Bande d'idiots", "language": "en"})
        self.assertGreater(response.context["output"]["result"]["score"], 0.5)
        response = self.client.post(url, {"task": "translation", "text": "Bonjour", "language": "en"})
        self.assertIn("GROQ_API_KEY", response.context["output"]["error"])

    def test_image_and_audio_tasks(self):
        url = reverse("enrichment:laboratory")
        response = self.client.post(url, {"task": "description", "text": "", "language": "en", "image": image_upload()})
        self.assertIn("Image", response.context["output"]["result"]["description"])
        VocabulaireIA.objects.create(term="Aïn Draham", variants="ain drahem")
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()):
            response = self.client.post(url, {"task": "transcription", "text": "", "language": "en", "audio": wav_upload()})
        self.assertIn("Aïn Draham", response.context["output"]["result"]["text"])

    def test_missing_input_and_prompt_mismatch(self):
        url = reverse("enrichment:laboratory")
        self.assertFalse(self.client.post(url, {"task": "summary", "text": "", "language": "en"}).context["form"].is_valid())
        prompt = PromptIA.objects.create(task="tags", name="x", template="Tags {text}")
        response = self.client.post(url, {"task": "summary", "text": "Bonjour", "language": "en", "prompt": prompt.pk})
        self.assertContains(response, "autre tâche")
        self.assertEqual(self.client.get(url, {"tache": "summary"}).context["form"].initial["task"], "summary")


class DigestTests(ModeratorClientMixin, SensaTestCase):
    def test_generate_local_digest_and_crud(self):
        self.client.force_login(self.member)
        self.publish(self.client, {"text": "Couscous au poisson préparé avec patience #cuisine"})
        self.client.force_login(self.moderator)
        today = timezone.localdate()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("enrichment:digest_create"),
                                        {"period_start": today - timedelta(days=7), "period_end": today})
        digest = DigestIA.objects.get()
        self.assertRedirects(response, reverse("enrichment:digest_detail", args=[digest.pk]))
        digest.refresh_from_db()
        self.assertEqual(digest.status, DigestIA.Status.DONE)
        self.assertEqual(digest.stats["posts"], 1)
        self.assertIn("cuisine", [tag["name"] for tag in digest.top_tags])
        self.assertEqual(digest.provider, "synthese-locale")
        self.assertContains(self.client.get(reverse("enrichment:digest_detail", args=[digest.pk])), "Synthèse IA")
        self.assertContains(self.client.get(reverse("enrichment:monitor")), "Dernier digest IA")
        self.client.post(reverse("enrichment:digest_delete", args=[digest.pk]))
        self.assertFalse(DigestIA.objects.exists())

    def test_digest_uses_llm_when_available(self):
        Publication.objects.create(author=self.member, text="Concert sur la plage", status=Publication.Status.READY)
        digest = DigestIA.objects.create(period_start=timezone.now() - timedelta(days=7), period_end=timezone.now())
        answer = mock.Mock(provider="groq", data={"title": "Une semaine musicale", "summary": "La plage a chanté.",
                                                   "highlights": [{"theme": "Musique", "description": "Concerts."}]})
        with mock.patch("core.ai.llm.is_configured", return_value=True), mock.patch("core.ai.llm.structured", return_value=answer):
            generate(digest)
        self.assertEqual((digest.title, digest.provider, digest.highlights[0]["theme"]), ("Une semaine musicale", "groq", "Musique"))

    def test_llm_failure_falls_back_and_invalid_period(self):
        Publication.objects.create(author=self.member, text="Bonjour", status=Publication.Status.READY)
        digest = DigestIA.objects.create(period_start=timezone.now() - timedelta(days=7), period_end=timezone.now())
        with mock.patch("core.ai.llm.is_configured", return_value=True), \
                mock.patch("core.ai.llm.structured", side_effect=AIError("panne")):
            generate(digest)
        self.assertEqual((digest.status, digest.provider), (DigestIA.Status.DONE, "synthese-locale"))
        today = timezone.localdate()
        self.client.post(reverse("enrichment:digest_create"), {"period_start": today, "period_end": today - timedelta(days=3)})
        self.assertEqual(DigestIA.objects.count(), 1)

    def test_weekly_digest_task(self):
        from .tasks import weekly_digest

        weekly_digest.apply()
        self.assertEqual(DigestIA.objects.get().status, DigestIA.Status.DONE)


class TranscriptionCorrectionTests(SensaTestCase):
    def setUp(self):
        self.author = make_member("lina")
        self.client.force_login(self.author)
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription("Bonjour de bizert, à demain.")):
            self.publish(self.client, {"text": ""}, {"audio": wav_upload()})
        self.audio = Audio.objects.get()

    def test_correct_then_revert(self):
        url = reverse("enrichment:transcription_edit", args=[self.audio.pk])
        self.assertContains(self.client.get(url), "Corriger la transcription")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"segment": ["Bonjour de Bizerte, à demain."], "note": "Nom de ville"})
        self.assertRedirects(response, f"{self.audio.publication.get_absolute_url()}#transcription", fetch_redirect_response=False)
        self.audio.refresh_from_db()
        self.assertEqual(self.audio.transcription, "Bonjour de Bizerte, à demain.")
        self.assertEqual(self.audio.segments[0]["start"], 0.0)
        correction = CorrectionTranscription.objects.get()
        self.assertEqual(correction.text_before, "Bonjour de bizert, à demain.")
        # Les étapes dépendantes ont été recalculées sur le texte corrigé.
        summary = AnalyseIA.objects.get(type=T.SUMMARY)
        self.assertIn("Bizerte", summary.result["summary"])
        self.assertEqual(Publication.objects.get(pk=self.audio.publication_id).status, Publication.Status.READY)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("enrichment:transcription_revert", args=[self.audio.pk]))
        self.audio.refresh_from_db()
        self.assertEqual(self.audio.transcription, "Bonjour de bizert, à demain.")
        self.assertTrue(CorrectionTranscription.objects.filter(is_revert=True).exists())

    def test_permissions_and_validation(self):
        url = reverse("enrichment:transcription_edit", args=[self.audio.pk])
        self.client.force_login(make_member("noah"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(make_member("camille", role=Membre.Role.MODERATEUR))
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"segment": ["   "]})
        self.assertContains(response, "ne peut pas être vide")
        response = self.client.post(url, {"segment": ["a", "b"]})
        self.assertContains(response, "rechargez la page")
