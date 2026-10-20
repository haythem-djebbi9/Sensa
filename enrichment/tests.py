from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from accounts.models import Membre
from core.ai import AIError, groq_provider, llm
from core.testing import SensaTestCase, image_upload, make_member, wav_upload
from publications.models import Audio, Publication
from social.models import Notification

from . import pipeline
from .models import AnalyseIA, CacheIA, PipelineConfig, PublicationTag, TentativeIA
from .subtitles import to_srt, to_vtt
from .watchdog import requeue_stuck

T = AnalyseIA.Type
S = AnalyseIA.Status


def fake_transcription(text="Bonjour à tous, voici le marché du matin."):
    return {
        "text": text, "language": "fr", "provider": "test", "model": "whisper-test",
        "segments": [{"start": 0.0, "end": 2.0, "text": text}],
    }


class PipelineTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.client.force_login(self.lina)

    def test_full_pipeline_with_audio(self):
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()) as transcribe:
            self.publish(self.client, {"text": "Ambiance #marche"}, {"audio": wav_upload()})
        transcribe.assert_called_once()
        post = Publication.objects.get()
        self.assertEqual(post.status, Publication.Status.READY)
        self.assertEqual(post.audio.transcription, "Bonjour à tous, voici le marché du matin.")
        rows = {row.type: row for row in post.analyses.all()}
        self.assertEqual(set(rows), {"transcription", "tags", "summary", "moderation", "embedding"})
        self.assertTrue(all(row.status == S.DONE for row in rows.values()))
        self.assertIn("marche", rows["tags"].result["tags"])
        self.assertTrue(rows["summary"].result["summary"].startswith("Bonjour à tous"))
        self.assertEqual(rows["transcription"].result["words"], 8)
        self.assertTrue(Notification.objects.filter(recipient=self.lina, type=Notification.Type.AI_READY).exists())

    def test_three_attempts_then_failed(self):
        with mock.patch("enrichment.ai.transcribe", side_effect=AIError("API indisponible")) as transcribe:
            self.publish(self.client, {"text": "Un vocal"}, {"audio": wav_upload()})
        self.assertEqual(transcribe.call_count, 3)
        post = Publication.objects.get()
        analysis = post.analyses.get(type=T.TRANSCRIPTION)
        self.assertEqual((analysis.status, analysis.attempts), (S.FAILED, 3))
        self.assertIn("API indisponible", analysis.error)
        # Journal des tentatives : 3 échecs horodatés.
        self.assertEqual(list(analysis.tentatives.values_list("success", flat=True)), [False, False, False])
        self.assertIsNotNone(analysis.finished_at)
        self.assertEqual(post.analyses.get(type=T.MODERATION).status, S.DONE)
        self.assertEqual(post.status, Publication.Status.FAILED)
        self.assertTrue(post.is_visible_to(make_member("noah")))
        self.assertTrue(Notification.objects.filter(recipient=self.lina, type=Notification.Type.AI_FAILED).exists())

    def test_success_on_second_attempt_is_logged(self):
        with mock.patch("enrichment.ai.transcribe", side_effect=[AIError("timeout"), fake_transcription()]):
            self.publish(self.client, {"text": ""}, {"audio": wav_upload()})
        analysis = AnalyseIA.objects.get(type=T.TRANSCRIPTION)
        self.assertEqual((analysis.status, analysis.attempts), (S.DONE, 2))
        attempts = list(analysis.tentatives.all())
        self.assertEqual([a.success for a in attempts], [False, True])
        self.assertEqual(attempts[1].provider, "test")
        self.assertEqual(analysis.model, "whisper-test")
        self.assertIsNotNone(analysis.duration_ms)
        self.assertEqual(Publication.objects.get().status, Publication.Status.READY)

    def test_retry_failed_publication(self):
        with mock.patch("enrichment.ai.transcribe", side_effect=AIError("down")):
            self.publish(self.client, {"text": ""}, {"audio": wav_upload()})
        post = Publication.objects.get()
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("publications:retry", args=[post.pk]))
        post.refresh_from_db()
        self.assertEqual(post.status, Publication.Status.READY)
        self.assertEqual(Audio.objects.get().transcription, fake_transcription()["text"])

    def test_retry_limit_follows_configuration(self):
        from moderation.models import ConfigurationIA

        config = ConfigurationIA.get_solo()
        config.max_retries = 2
        config.save()
        with mock.patch("enrichment.ai.transcribe", side_effect=AIError("down")) as transcribe:
            self.publish(self.client, {"text": ""}, {"audio": wav_upload()})
        self.assertEqual(transcribe.call_count, 2)

    def test_launch_is_idempotent_per_target(self):
        post = Publication.objects.create(author=self.lina, text="Bonjour")
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                pipeline.launch(post)
        self.assertEqual(post.analyses.count(), 3)

    def test_long_text_gets_summary(self):
        long_text = "La randonnée commence au lever du jour. " * 10
        self.publish(self.client, {"text": long_text})
        summary = AnalyseIA.objects.get(type=T.SUMMARY)
        self.assertEqual(summary.status, S.DONE)
        self.assertLessEqual(len(summary.result["summary"]), 245)

    def test_steps_can_be_disabled(self):
        config = PipelineConfig.get_solo()
        config.generate_tags = False
        config.transcribe_audio = False
        config.save()
        with mock.patch("enrichment.ai.transcribe") as transcribe:
            self.publish(self.client, {"text": "Bonjour #test"}, {"audio": wav_upload()})
        transcribe.assert_not_called()
        types = set(AnalyseIA.objects.values_list("type", flat=True))
        self.assertEqual(types, {"summary", "moderation", "embedding"})
        self.assertEqual(Publication.objects.get().status, Publication.Status.READY)

    def test_tags_keep_author_and_ai_sources(self):
        self.publish(self.client, {"text": "Couscous au poisson préparé avec patience #cuisine"})
        post = Publication.objects.get()
        sources = dict(PublicationTag.objects.filter(publication=post).values_list("tag__name", "source"))
        self.assertEqual(sources["cuisine"], "auteur")
        self.assertIn("ia", sources.values())
        insights = post.analyses.get(type=T.TAGS).result
        self.assertEqual(insights["language"], "fr")


class CacheTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.client.force_login(self.lina)

    def test_same_audio_transcribed_once(self):
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()) as transcribe:
            self.publish(self.client, {"text": "premier"}, {"audio": wav_upload(name="a.wav")})
            self.publish(self.client, {"text": "second"}, {"audio": wav_upload(name="b.wav")})
        transcribe.assert_called_once()
        second = AnalyseIA.objects.filter(type=T.TRANSCRIPTION).order_by("created_at").last()
        self.assertTrue(second.cached)
        self.assertEqual(second.provider, "cache")
        self.assertEqual(CacheIA.objects.get().hits, 1)
        self.assertEqual(Audio.objects.order_by("created_at").last().transcription, fake_transcription()["text"])

    def test_cache_can_be_disabled(self):
        config = PipelineConfig.get_solo()
        config.use_cache = False
        config.save()
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()) as transcribe:
            self.publish(self.client, {"text": "premier"}, {"audio": wav_upload()})
            self.publish(self.client, {"text": "second"}, {"audio": wav_upload()})
        self.assertEqual(transcribe.call_count, 2)
        self.assertFalse(CacheIA.objects.exists())

    @override_settings(GROQ_API_KEY="gsk-test")
    def test_same_image_described_once(self):
        with mock.patch("publications.ai.describe_with_llm", return_value=("Un carré violet.", "groq")) as describe, \
                mock.patch("core.ai.llm.structured", side_effect=AIError("hors test")):
            self.publish(self.client, {"text": ""}, {"image": image_upload()})
            self.publish(self.client, {"text": ""}, {"image": image_upload()})
        describe.assert_called_once()
        rows = AnalyseIA.objects.filter(type=T.DESCRIPTION).order_by("created_at")
        self.assertEqual([row.cached for row in rows], [False, True])


class WatchdogTests(SensaTestCase):
    def test_requeues_stuck_running_analysis(self):
        lina = make_member("lina")
        post = Publication.objects.create(author=lina, text="Bonjour à tous")
        with self.captureOnCommitCallbacks(execute=True):
            pipeline.launch(post)
        stuck = post.analyses.get(type=T.TAGS)
        AnalyseIA.objects.filter(pk=stuck.pk).update(status=S.RUNNING)
        AnalyseIA.objects.filter(pk=stuck.pk).update(updated_at=stuck.updated_at.replace(year=2020))
        Publication.objects.filter(pk=post.pk).update(status=Publication.Status.PENDING)
        self.assertEqual(requeue_stuck(), 1)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, S.DONE)
        post.refresh_from_db()
        self.assertEqual(post.status, Publication.Status.READY)

    def test_nothing_to_do(self):
        self.assertEqual(requeue_stuck(), 0)


class SubtitleAndTranslationTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.post = Publication.objects.create(author=self.lina, text="", status=Publication.Status.READY)
        self.audio = Audio.objects.create(
            publication=self.post, file="publications/audio/x.wav", duration=6, language="fr",
            transcription="Bonjour. Au revoir.",
            segments=[{"start": 0.0, "end": 1.5, "text": "Bonjour."}, {"start": 61.25, "end": 0, "text": "Au revoir."}],
        )
        self.client.force_login(make_member("noah"))

    def test_vtt_and_srt_formats(self):
        vtt = to_vtt(self.audio)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:00.000 --> 00:00:01.500\nBonjour.", vtt)
        self.assertIn("00:01:01.250 --> 00:01:05.250", vtt)
        srt = to_srt(self.audio)
        self.assertIn("1\n00:00:00,000 --> 00:00:01,500\nBonjour.", srt)

    def test_subtitle_download_respects_visibility(self):
        url = reverse("enrichment:subtitles", args=[self.audio.pk, "vtt"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("enrichment:subtitles", args=[self.audio.pk, "exe"])).status_code, 404)
        Publication.objects.filter(pk=self.post.pk).update(status=Publication.Status.BLOCKED)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_translation_on_demand(self):
        with mock.patch("enrichment.ai.translate", return_value=("Hello. Goodbye.", "groq")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("enrichment:translate", args=[self.audio.pk]), {"lang": "en"})
        self.assertRedirects(response, f"{self.post.get_absolute_url()}#traductions", fetch_redirect_response=False)
        translation = AnalyseIA.objects.get(type=T.TRANSLATION)
        self.assertEqual(translation.target_id, f"{self.audio.pk}:en")
        self.assertEqual(translation.result["text"], "Hello. Goodbye.")
        # Une traduction ne change pas le statut de la publication.
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Publication.Status.READY)
        self.assertContains(self.client.get(self.post.get_absolute_url()), "Hello. Goodbye.")

    def test_translation_without_provider_fails_cleanly(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("enrichment:translate", args=[self.audio.pk]), {"lang": "ar"})
        translation = AnalyseIA.objects.get(type=T.TRANSLATION)
        self.assertEqual(translation.status, S.FAILED)
        self.assertEqual(translation.attempts, 3)


class GroqProviderTests(SensaTestCase):
    def setUp(self):
        self.chat = mock.Mock()
        self.transcriptions = mock.Mock()
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.chat)),
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=self.transcriptions)),
        )
        patcher = mock.patch.object(groq_provider, "get_client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)

    @override_settings(GROQ_API_KEY="gsk-test", SENSA_GROQ_TEXT_MODEL="openai/gpt-oss-120b")
    def test_structured_json_schema(self):
        self.chat.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"tone": "positive", "score": 0.9}'))],
            usage=SimpleNamespace(total_tokens=42),
        )
        result = llm.structured("Classe", {"type": "object"})
        self.assertEqual((result.data["tone"], result.provider), ("positive", "groq"))
        kwargs = self.chat.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-oss-120b")
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertEqual(kwargs["reasoning_effort"], "low")

    @override_settings(GROQ_API_KEY="gsk-test", SENSA_GROQ_VISION_MODEL="qwen/qwen3.8-27b")
    def test_vision_parses_json_from_text(self):
        self.chat.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='<think>…</think>Voici : {"description": "Un port."}'))],
            usage=None,
        )
        result = groq_provider.structured("Décris", {"type": "object"}, image=(b"jpeg", "image/jpeg"))
        self.assertEqual(result["description"], "Un port.")
        content = self.chat.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(content[1]["image_url"]["url"][:23], "data:image/jpeg;base64,")

    @override_settings(GROQ_API_KEY="gsk-test")
    def test_whisper_transcription(self):
        payload = {"text": " Bonjour ", "language": "French", "duration": 3.2,
                   "segments": [{"start": 0, "end": 1.2, "text": " Bonjour "}, {"start": 1.2, "end": 2, "text": " "}]}
        self.transcriptions.return_value = SimpleNamespace(to_dict=lambda: payload)
        output = groq_provider.transcribe("a.wav", b"RIFF", language="fr")
        self.assertEqual(output["text"], "Bonjour")
        self.assertEqual(output["language"], "fr")
        self.assertEqual(output["segments"], [{"start": 0.0, "end": 1.2, "text": "Bonjour"}])
        self.assertEqual(self.transcriptions.call_args.kwargs["response_format"], "verbose_json")

    @override_settings(GROQ_API_KEY="gsk-test", ANTHROPIC_API_KEY=None)
    def test_router_raises_when_all_providers_fail(self):
        self.chat.side_effect = AIError("panne")
        with self.assertRaises(AIError):
            llm.structured("x", {"type": "object"})


class DashboardTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.client.force_login(self.lina)
        with mock.patch("enrichment.ai.transcribe", side_effect=AIError("down")):
            self.publish(self.client, {"text": "Bonjour"}, {"audio": wav_upload()})
        self.moderator = make_member("camille", role=Membre.Role.MODERATEUR)
        self.client.force_login(self.moderator)

    def test_dashboard_stats(self):
        response = self.client.get(reverse("enrichment:monitor"))
        stats = response.context["stats"]
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(len(stats["timeline"]), 24)
        self.assertEqual(sum(bucket["total"] for bucket in stats["timeline"]), stats["total"])
        labels = {row["type"]: row for row in stats["per_type"]}
        self.assertEqual(labels["transcription"]["success_rate"], 0)
        self.assertContains(response, "Analyses terminées par heure")
        # Les pourcentages des styles CSS ne doivent jamais être localisés (« 100,0% » serait ignoré).
        self.assertNotRegex(response.content.decode(), r"(height|width): \d+,\d+%")
        self.assertIsNone(response.context["config_form"])  # réservé aux administrateurs

    def test_analysis_detail_and_retry_all(self):
        failed = AnalyseIA.objects.get(status=S.FAILED)
        response = self.client.get(reverse("enrichment:analysis_detail", args=[failed.pk]))
        self.assertContains(response, "Tentative 3")
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("enrichment:retry_all_failed"))
        failed.refresh_from_db()
        self.assertEqual(failed.status, S.DONE)
        # L'historique des tentatives est conservé : 3 échecs puis 1 succès.
        self.assertEqual(list(TentativeIA.objects.filter(analyse=failed).values_list("success", flat=True)), [False, False, False, True])
        self.assertEqual(Publication.objects.get().status, Publication.Status.READY)

    def test_config_update_admin_only(self):
        url = reverse("enrichment:config")
        data = {"describe_images": "on", "transcribe_audio": "on", "generate_tags": "on", "summarize": "",
                "max_tags": 4, "whisper_model": "whisper-large-v3", "language_hint": "fr", "use_cache": "on",
                "stuck_after_minutes": 15}
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(make_member("avery", role=Membre.Role.ADMIN))
        self.client.post(url, data)
        config = PipelineConfig.get_solo()
        self.assertEqual((config.summarize, config.max_tags, config.whisper_model, config.language_hint), (False, 4, "whisper-large-v3", "fr"))

    def test_management_commands(self):
        out = StringIO()
        with mock.patch("enrichment.ai.transcribe", return_value=fake_transcription()):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("reenrich", "--failed", stdout=out)
        self.assertIn("1 analyse(s) en échec relancée(s)", out.getvalue())
        out = StringIO()
        call_command("pipeline_stats", stdout=out)
        self.assertIn("Transcription audio", out.getvalue())
        out = StringIO()
        call_command("pipeline_watchdog", stdout=out)
        self.assertIn("0 analyse(s)", out.getvalue())
