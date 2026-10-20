from unittest import mock

from django.urls import reverse

from accounts.models import Membre
from core.ai.text import lexicon_toxicity
from core.testing import SensaTestCase, make_member
from enrichment.models import AnalyseIA
from publications.models import Publication
from social.models import Notification

from .models import ConfigurationIA, DecisionModeration, Signalement
from .services import score_toxicity


class ToxicityTests(SensaTestCase):
    def test_lexicon_scores(self):
        self.assertEqual(score_toxicity("Une belle journée au soleil"), 0.0)
        self.assertGreaterEqual(score_toxicity("Vous êtes des idiots et des crétins"), 0.5)
        self.assertGreaterEqual(score_toxicity("Je vais te tuer"), 0.85)
        score, terms = lexicon_toxicity("bande de CONNARDS")
        self.assertGreaterEqual(score, 0.85)
        self.assertIn("connards", terms)

    def test_score_uses_transcription_and_description(self):
        self.assertGreater(score_toxicity("Écoutez mon vocal", "", "espèce d'imbécile"), 0.5)

    def test_thresholds(self):
        config = ConfigurationIA.get_solo()
        self.assertEqual(config.decide(0.2), "ALLOW")
        self.assertEqual(config.decide(0.5), "REVIEW")
        self.assertEqual(config.decide(0.84), "REVIEW")
        self.assertEqual(config.decide(0.85), "BLOCK")

    def test_pipeline_applies_decision(self):
        lina = make_member("lina")
        self.client.force_login(lina)
        cases = [(0.1, "READY"), (0.6, "UNDER_REVIEW"), (0.95, "BLOCKED")]
        for score, expected in cases:
            verdict = {"score": score, "labels": [], "justification": "", "provider": "test"}
            with mock.patch("moderation.tasks.analyse_toxicity", return_value=verdict):
                self.publish(self.client, {"text": f"texte {score}"})
            post = Publication.objects.latest("created_at")
            self.assertEqual(post.status, expected)
            self.assertEqual(post.analyses.get(type=AnalyseIA.Type.MODERATION).score, score)


class QueueTests(SensaTestCase):
    def setUp(self):
        self.author = make_member("noah")
        self.moderator = make_member("camille", role=Membre.Role.MODERATEUR)
        self.risky = Publication.objects.create(author=self.author, text="risqué", status=Publication.Status.UNDER_REVIEW)
        self.calm = Publication.objects.create(author=self.author, text="calme", status=Publication.Status.UNDER_REVIEW)
        AnalyseIA.objects.create(publication=self.risky, type="moderation", target_id=str(self.risky.pk), status="DONE", score=0.8, result={"decision": "REVIEW"})
        AnalyseIA.objects.create(publication=self.calm, type="moderation", target_id=str(self.calm.pk), status="DONE", score=0.55, result={"decision": "REVIEW"})
        self.client.force_login(self.moderator)

    def test_queue_sorted_by_risk(self):
        response = self.client.get(reverse("moderation:queue"))
        items = list(response.context["page"])
        self.assertEqual(items, [self.risky, self.calm])
        self.assertEqual(response.context["metrics"]["awaiting"], 2)

    def test_approve_and_block(self):
        self.client.post(reverse("moderation:decide", args=[self.calm.pk]), {"action": "approve"})
        self.client.post(reverse("moderation:decide", args=[self.risky.pk]), {"action": "block"})
        self.calm.refresh_from_db()
        self.risky.refresh_from_db()
        self.assertEqual(self.calm.status, Publication.Status.READY)
        self.assertEqual(self.risky.status, Publication.Status.BLOCKED)
        self.assertEqual(DecisionModeration.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(recipient=self.author, type="moderation").count(), 2)

    def test_member_cannot_decide(self):
        self.client.force_login(make_member("lina"))
        response = self.client.post(reverse("moderation:decide", args=[self.calm.pk]), {"action": "approve"})
        self.assertEqual(response.status_code, 403)


class ReportTests(SensaTestCase):
    def test_reports_escalate_to_review_and_are_resolved(self):
        author = make_member("noah")
        post = Publication.objects.create(author=author, text="contenu", status=Publication.Status.READY)
        reporters = [make_member(f"membre{i}") for i in range(3)]
        for index, reporter in enumerate(reporters):
            self.client.force_login(reporter)
            self.client.post(reverse("moderation:report", args=[post.pk]), {"reason": "SPAM"})
            post.refresh_from_db()
            expected = Publication.Status.UNDER_REVIEW if index == 2 else Publication.Status.READY
            self.assertEqual(post.status, expected)
        # Un second signalement du même membre est ignoré.
        self.client.post(reverse("moderation:report", args=[post.pk]), {"reason": "HATE"})
        self.assertEqual(Signalement.objects.count(), 3)

        moderator = make_member("camille", role=Membre.Role.MODERATEUR)
        self.client.force_login(moderator)
        self.assertEqual(list(self.client.get(reverse("moderation:queue"), {"filtre": "signales"}).context["page"]), [post])
        self.client.post(reverse("moderation:decide", args=[post.pk]), {"action": "dismiss"})
        self.assertFalse(Signalement.objects.filter(status=Signalement.Status.OPEN).exists())
        self.assertEqual(Signalement.objects.filter(handled_by=moderator).count(), 3)

    def test_cannot_report_own_post(self):
        author = make_member("noah")
        post = Publication.objects.create(author=author, text="contenu", status=Publication.Status.READY)
        self.client.force_login(author)
        self.client.post(reverse("moderation:report", args=[post.pk]), {"reason": "SPAM"})
        self.assertFalse(Signalement.objects.exists())


class AdminSettingsTests(SensaTestCase):
    def setUp(self):
        self.admin = make_member("avery", role=Membre.Role.ADMIN)
        self.client.force_login(self.admin)

    def test_update_thresholds(self):
        response = self.client.post(reverse("moderation:settings"), {"review_percent": 40, "block_percent": 90, "max_retries": 4})
        self.assertRedirects(response, reverse("moderation:settings"))
        config = ConfigurationIA.get_solo()
        self.assertEqual((config.threshold_review, config.threshold_block, config.max_retries), (0.4, 0.9, 4))
        self.assertEqual(config.updated_by, self.admin)

    def test_invalid_thresholds(self):
        response = self.client.post(reverse("moderation:settings"), {"review_percent": 90, "block_percent": 50, "max_retries": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConfigurationIA.get_solo().threshold_review, 0.5)

    def test_change_role_and_deactivate(self):
        member = make_member("lina")
        self.client.post(reverse("moderation:account_update", args=[member.pk]), {"role": "MODERATEUR", "is_active": "on"})
        member.refresh_from_db()
        self.assertEqual(member.role, Membre.Role.MODERATEUR)
        self.client.post(reverse("moderation:account_update", args=[member.pk]), {"role": "MODERATEUR", "is_active": ""})
        member.refresh_from_db()
        self.assertFalse(member.is_active)

    def test_admin_cannot_demote_self(self):
        self.client.post(reverse("moderation:account_update", args=[self.admin.pk]), {"role": "MEMBRE", "is_active": "on"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Membre.Role.ADMIN)

    def test_settings_page_shows_health(self):
        response = self.client.get(reverse("moderation:settings"))
        self.assertContains(response, "Santé du système")
        self.assertIn(response.context["health"]["overall"], {"ok", "warn", "down"})
