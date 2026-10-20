"""Tests du client Claude partagé (sans appel réseau : le client est simulé)."""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from core.ai import AIError, claude
from core.testing import image_upload, make_member
from moderation.services import analyse_toxicity
from publications import ai as publications_ai
from publications.models import Image, Publication
from social.ai import classify_tone


def fake_response(payload, stop_reason="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(payload))]
    return SimpleNamespace(stop_reason=stop_reason, content=content if stop_reason != "refusal" else [])


@override_settings(ANTHROPIC_API_KEY="sk-test", SENSA_CLAUDE_MODEL="claude-opus-5-5")
class ClaudeClientTests(TestCase):
    def setUp(self):
        self.create = mock.Mock()
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=self.create)))
        patcher = mock.patch.object(claude, "get_client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_shape_and_json_parsing(self):
        self.create.return_value = fake_response({"tone": "positive", "score": 0.9})
        self.assertEqual(classify_tone("Merci !"), ("positive", 0.9, "claude"))
        request = self.create.call_args.kwargs
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(request["fallbacks"], "default")
        self.assertEqual(request["output_config"]["effort"], "low")
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("thinking", request)

    def test_refusal_falls_back_to_local_classifier(self):
        self.create.return_value = fake_response({}, stop_reason="refusal")
        with self.assertRaises(AIError):
            claude.structured("x", {"type": "object"})
        verdict = analyse_toxicity("Bande d'idiots", last_attempt=True)
        self.assertEqual(verdict["provider"], "lexique-local")
        self.assertGreater(verdict["score"], 0.5)

    def test_error_raised_while_retries_remain(self):
        self.create.return_value = fake_response({}, stop_reason="max_tokens")
        with self.assertRaises(AIError):
            analyse_toxicity("texte", last_attempt=False)

    def test_vision_sends_resized_jpeg(self):
        self.create.return_value = fake_response({"description": "Un carré violet uni."})
        post = Publication.objects.create(author=make_member("lina"), text="")
        image = Image.objects.create(publication=post, file=image_upload(size=(3000, 2000)))
        description, provider = publications_ai.describe_image(image)
        self.assertEqual((description, provider), ("Un carré violet uni.", "claude"))
        block = self.create.call_args.kwargs["messages"][0]["content"][0]
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["media_type"], "image/jpeg")
