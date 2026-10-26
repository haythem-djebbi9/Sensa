from unittest import mock

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from core.ai import AIError
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse

from accounts.models import Membre
from core.testing import SensaTestCase, make_member
from publications.models import Publication

from .ai import quick_tone
from .consumers import NotificationConsumer
from .models import Abonnement, Commentaire, CommentTranslation, ConversationInsight, Notification, Reaction
from .services import group_name
from .tasks import TONE_PRIORITY


class FollowLikeTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.maya = make_member("maya")
        self.post = Publication.objects.create(author=self.maya, text="Chanson", status=Publication.Status.READY)
        self.client.force_login(self.lina)

    def test_follow_toggle_and_notification(self):
        url = reverse("social:follow_toggle", args=["maya"])
        response = self.client.post(url, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.json(), {"following": True, "followers": 1})
        self.assertTrue(Notification.objects.filter(recipient=self.maya, type="follow", actor=self.lina).exists())
        self.client.post(url)
        self.assertFalse(Abonnement.objects.exists())

    def test_cannot_follow_self(self):
        self.client.post(reverse("social:follow_toggle", args=["lina"]))
        self.assertFalse(Abonnement.objects.exists())

    def test_like_toggle_json_and_single_notification(self):
        url = reverse("social:like_toggle", args=[self.post.pk])
        self.assertEqual(self.client.post(url, HTTP_X_REQUESTED_WITH="fetch").json(), {"liked": True, "count": 1})
        self.assertEqual(self.client.post(url, HTTP_X_REQUESTED_WITH="fetch").json(), {"liked": False, "count": 0})
        self.client.post(url, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(Reaction.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(type="like").count(), 1)

    def test_cannot_like_hidden_post(self):
        Publication.objects.filter(pk=self.post.pk).update(status=Publication.Status.BLOCKED)
        response = self.client.post(reverse("social:like_toggle", args=[self.post.pk]))
        self.assertEqual(response.status_code, 404)


class FollowListTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.maya = make_member("maya")
        self.noah = make_member("noah")
        Abonnement.objects.create(follower=self.lina, followed=self.maya)
        Abonnement.objects.create(follower=self.noah, followed=self.maya)

    def test_followers_page_lists_members_who_follow_the_profile(self):
        self.client.force_login(self.maya)
        response = self.client.get(reverse("social:followers", args=["maya"]))
        self.assertContains(response, "Abonnés de maya")
        self.assertEqual(set(response.context["page"].object_list), {self.lina, self.noah})

    def test_following_page_lists_the_profile_subscriptions(self):
        self.client.force_login(self.lina)
        response = self.client.get(reverse("social:following", args=["lina"]))
        self.assertContains(response, "Abonnements de lina")
        self.assertEqual(set(response.context["page"].object_list), {self.maya})
        self.assertIn(self.maya.pk, set(response.context["my_following"]))


class CommentTests(SensaTestCase):
    def setUp(self):
        self.lina = make_member("lina")
        self.maya = make_member("maya")
        self.post = Publication.objects.create(author=self.maya, text="Chanson", status=Publication.Status.READY)
        self.client.force_login(self.lina)
        self.url = reverse("social:comment_create", args=[self.post.pk])

    def test_comment_tone_and_notification_priority(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {"text": "Magnifique, merci pour ce partage !"})
        comment = Commentaire.objects.get()
        self.assertRedirects(response, f"{self.post.get_absolute_url()}#comment-{comment.pk}", fetch_redirect_response=False)
        self.assertEqual(comment.tone, Commentaire.Tone.POSITIVE)
        self.assertIsNotNone(comment.tone_score)
        notification = Notification.objects.get(type="comment")
        self.assertEqual(notification.priority, Notification.Priority.HIGH)

    def test_be_kind_warning_before_aggressive_comment(self):
        response = self.client.post(self.url, {"text": "Tu es un idiot et un crétin"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["be_kind"])
        self.assertContains(response, "Restons bienveillants")
        self.assertFalse(Commentaire.objects.exists())
        # Confirmation explicite : le commentaire est publié, classé agressif, notification en basse priorité.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {"text": "Tu es un idiot et un crétin", "confirm": "1"})
        comment = Commentaire.objects.get()
        self.assertEqual(comment.tone, Commentaire.Tone.AGGRESSIVE)
        self.assertEqual(Notification.objects.get(type="comment").priority, Notification.Priority.LOW)

    def test_quick_tone(self):
        self.assertEqual(quick_tone("Bravo, superbe photo")[0], "positive")
        self.assertEqual(quick_tone("Dommage, c'est un peu ennuyeux")[0], "negative")
        self.assertEqual(quick_tone("À quelle heure ?")[0], "neutral")
        self.assertEqual(quick_tone("ta gueule")[0], "aggressive")

    def test_ai_reply_suggestions_use_comment_context(self):
        target = Commentaire.objects.create(publication=self.post, author=self.maya, text="Quel instrument avez-vous utilisé ?")
        context = Commentaire.objects.create(publication=self.post, author=self.lina, text="Le morceau est magnifique.")
        suggestions = ["Merci ! J'ai utilisé un piano.", "C'est un vieux piano droit.", "Je peux partager les détails."]
        with mock.patch("social.views.ai.suggest_replies", return_value=(suggestions, "groq")) as suggest:
            response = self.client.post(reverse("social:reply_suggestions", args=[target.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"suggestions": suggestions, "provider": "groq"})
        suggest.assert_called_once_with(
            "Chanson",
            target.text,
            [{"author": context.author.display_name, "text": context.text}],
        )

    def test_ai_reply_suggestions_report_provider_error(self):
        target = Commentaire.objects.create(publication=self.post, author=self.maya, text="Merci")
        with mock.patch("social.views.ai.suggest_replies", side_effect=AIError("provider unavailable")):
            response = self.client.post(reverse("social:reply_suggestions", args=[target.pk]))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "Suggestions indisponibles pour le moment."})

    def test_ai_reply_suggestions_respect_post_visibility(self):
        target = Commentaire.objects.create(publication=self.post, author=self.maya, text="Merci")
        Publication.objects.filter(pk=self.post.pk).update(status=Publication.Status.BLOCKED)
        response = self.client.post(reverse("social:reply_suggestions", args=[target.pk]))
        self.assertEqual(response.status_code, 404)

    def test_comments_can_reply_to_comments_and_notify_parent_author(self):
        parent = Commentaire.objects.create(publication=self.post, author=self.maya, text="Question initiale")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {"text": "Voici ma réponse", "parent_id": parent.pk})
        reply = Commentaire.objects.get(parent=parent)
        self.assertEqual(reply.publication, self.post)
        self.assertEqual(reply.author, self.lina)
        self.assertRedirects(
            response, f"{self.post.get_absolute_url()}#comment-{reply.pk}", fetch_redirect_response=False
        )
        self.assertTrue(Notification.objects.filter(recipient=self.maya, commentaire=reply).exists())
        detail = self.client.get(self.post.get_absolute_url())
        self.assertContains(detail, f'id="reply-form-{parent.pk}"')
        self.assertContains(detail, f'name="parent_id" value="{parent.pk}"')
        self.assertContains(
            detail,
            f'data-reply-trigger aria-expanded="false" aria-controls="reply-form-{parent.pk}"',
        )
        self.assertContains(detail, f'data-comment-username="{parent.author.username}"')
        self.assertContains(detail, f'href="{parent.author.get_absolute_url()}">@{parent.author.username}</a></small>')

    def test_nested_replies_keep_their_parent(self):
        root = Commentaire.objects.create(publication=self.post, author=self.maya, text="Commentaire racine")
        child = Commentaire.objects.create(publication=self.post, author=self.lina, parent=root, text="Réponse")
        self.client.force_login(self.maya)
        response = self.client.post(self.url, {"text": "Réponse imbriquée", "parent_id": child.pk})
        nested = Commentaire.objects.get(text="Réponse imbriquée")
        self.assertEqual(nested.parent, child)
        self.assertRedirects(
            response, f"{self.post.get_absolute_url()}#comment-{nested.pk}", fetch_redirect_response=False
        )

    def test_cannot_reply_to_comment_on_another_publication(self):
        other_post = Publication.objects.create(author=self.maya, text="Autre post", status=Publication.Status.READY)
        parent = Commentaire.objects.create(publication=other_post, author=self.maya, text="Autre conversation")
        response = self.client.post(self.url, {"text": "Réponse hors sujet", "parent_id": parent.pk})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Commentaire.objects.filter(text="Réponse hors sujet").exists())

    def test_comment_translation_is_saved_and_shown(self):
        comment = Commentaire.objects.create(publication=self.post, author=self.lina, text="Bonjour à tous")
        url = reverse("social:comment_translate", args=[comment.pk, "en"])
        with mock.patch("social.views.ai.translate_comment", return_value=("Hello everyone", "groq")):
            response = self.client.post(url)
        self.assertRedirects(
            response, f"{self.post.get_absolute_url()}#comment-{comment.pk}", fetch_redirect_response=False
        )
        translation = CommentTranslation.objects.get(comment=comment, language="en")
        self.assertEqual(translation.text, "Hello everyone")
        self.assertEqual(translation.provider, "groq")
        self.assertContains(self.client.get(self.post.get_absolute_url()), "Hello everyone")

    def test_comment_translation_reports_ai_errors(self):
        comment = Commentaire.objects.create(publication=self.post, author=self.lina, text="Bonjour")
        with mock.patch("social.views.ai.translate_comment", side_effect=AIError("service indisponible")):
            response = self.client.post(reverse("social:comment_translate", args=[comment.pk, "fr"]))
        self.assertRedirects(
            response, f"{self.post.get_absolute_url()}#comment-{comment.pk}", fetch_redirect_response=False
        )
        self.assertFalse(CommentTranslation.objects.exists())
        self.assertContains(self.client.get(self.post.get_absolute_url()), "Impossible de traduire le commentaire")

    def test_conversation_summary_and_starters_are_generated_for_post_author(self):
        Commentaire.objects.create(publication=self.post, author=self.lina, text="Très belle chanson")
        self.client.force_login(self.maya)
        url = reverse("social:conversation_insight", args=[self.post.pk])
        generated = (
            "Les lecteurs apprécient la chanson.",
            ["Comment avez-vous composé cette chanson ?", "Quel instrument ?", "Où avez-vous enregistré ?"],
            "claude",
        )
        with mock.patch("social.views.ai.summarize_conversation", return_value=generated) as summarize:
            response = self.client.post(url)
        self.assertRedirects(response, f"{self.post.get_absolute_url()}#commentaires", fetch_redirect_response=False)
        insight = ConversationInsight.objects.get(publication=self.post)
        self.assertEqual(insight.summary, generated[0])
        self.assertEqual(insight.starters, generated[1])
        self.assertEqual(insight.source_comment_count, 1)
        summarize.assert_called_once()
        self.assertContains(self.client.get(self.post.get_absolute_url()), generated[0])
        self.assertContains(self.client.get(self.post.get_absolute_url()), generated[1][0])

    def test_only_post_author_can_generate_conversation_insight(self):
        Commentaire.objects.create(publication=self.post, author=self.lina, text="Belle chanson")
        response = self.client.post(reverse("social:conversation_insight", args=[self.post.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ConversationInsight.objects.exists())

    def test_conversation_insight_requires_comments(self):
        self.client.force_login(self.maya)
        response = self.client.post(reverse("social:conversation_insight", args=[self.post.pk]))
        self.assertRedirects(response, f"{self.post.get_absolute_url()}#commentaires", fetch_redirect_response=False)
        self.assertFalse(ConversationInsight.objects.exists())

    def test_delete_comment_permissions(self):
        comment = Commentaire.objects.create(publication=self.post, author=self.maya, text="Merci")
        url = reverse("social:comment_delete", args=[comment.pk])
        self.client.post(url)  # lina n'est ni l'autrice du commentaire ni du post
        self.assertTrue(Commentaire.objects.exists())
        self.client.force_login(self.maya)
        self.client.post(url)
        self.assertFalse(Commentaire.objects.exists())


class NotificationViewsTests(SensaTestCase):
    def test_list_open_and_mark_all_read(self):
        lina, maya = make_member("lina"), make_member("maya")
        post = Publication.objects.create(author=lina, text="Bonjour", status=Publication.Status.READY)
        first = Notification.objects.create(recipient=lina, actor=maya, type="like", publication=post)
        Notification.objects.create(recipient=lina, actor=maya, type="follow")
        self.client.force_login(lina)
        self.assertEqual(self.client.get(reverse("social:notifications_count")).json(), {"unread": 2})
        response = self.client.get(reverse("social:notification_open", args=[first.pk]))
        self.assertRedirects(response, post.get_absolute_url())
        self.client.post(reverse("social:notifications_mark_all_read"))
        self.assertFalse(Notification.objects.filter(is_read=False).exists())
        self.assertContains(self.client.get(reverse("social:notifications")), "maya a aimé votre publication")

    def test_mark_all_read_json_response(self):
        lina, maya = make_member("lina"), make_member("maya")
        Notification.objects.create(recipient=lina, actor=maya, type="follow")
        self.client.force_login(lina)
        response = self.client.post(
            reverse("social:notifications_mark_all_read"), HTTP_X_REQUESTED_WITH="fetch"
        )
        self.assertEqual(response.json(), {"unread": 0})
        self.assertFalse(Notification.objects.filter(recipient=lina, is_read=False).exists())

    def test_open_comment_notification_marks_it_read_and_anchors_the_comment(self):
        lina, maya = make_member("lina"), make_member("maya")
        post = Publication.objects.create(author=lina, text="Bonjour", status=Publication.Status.READY)
        comment = Commentaire.objects.create(publication=post, author=maya, text="Merci")
        notification = Notification.objects.create(
            recipient=lina, actor=maya, type="comment", publication=post, commentaire=comment
        )
        self.client.force_login(lina)
        response = self.client.get(reverse("social:notification_open", args=[notification.pk]))
        self.assertRedirects(response, f"{post.get_absolute_url()}#comment-{comment.pk}")
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_notification_title_and_icon_depend_on_the_type(self):
        lina, maya = make_member("lina"), make_member("maya")
        follow = Notification.objects.create(recipient=lina, actor=maya, type="follow")
        ready = Notification.objects.create(recipient=lina, type="ai_ready")
        self.assertEqual(follow.title, "maya vous suit désormais")
        self.assertEqual(follow.icon, ("user", "mint"))
        self.assertEqual(ready.icon, ("spark", "ai"))


class CommentModerationTests(SensaTestCase):
    def setUp(self):
        self.post_author = make_member("maya")
        self.commenter = make_member("lina")
        self.moderator = make_member("camille", role=Membre.Role.MODERATEUR)
        self.post = Publication.objects.create(author=self.post_author, text="Chanson", status=Publication.Status.READY)
        self.comment = Commentaire.objects.create(publication=self.post, author=self.commenter, text="Merci")

    def test_moderator_can_delete_any_comment(self):
        self.assertTrue(self.comment.can_delete(self.moderator))

    def test_anonymous_cannot_delete_a_comment(self):
        self.assertFalse(self.comment.can_delete(AnonymousUser()))

    def test_tone_priority_covers_every_tone(self):
        self.assertEqual(set(TONE_PRIORITY), set(Commentaire.Tone.values))
        self.assertEqual(TONE_PRIORITY["positive"], Notification.Priority.HIGH)
        self.assertEqual(TONE_PRIORITY["aggressive"], Notification.Priority.LOW)


class SocialModelLabelTests(SensaTestCase):
    def test_translation_and_insight_have_readable_labels(self):
        maya = make_member("maya")
        post = Publication.objects.create(author=maya, text="Bonjour", status=Publication.Status.READY)
        comment = Commentaire.objects.create(publication=post, author=maya, text="Merci")
        translation = CommentTranslation.objects.create(comment=comment, language="en", text="Thanks")
        insight = ConversationInsight.objects.create(publication=post, summary="Résumé")
        self.assertIn("Anglais", str(translation))
        self.assertIn(str(comment), str(translation))
        self.assertIn("Synthèse", str(insight))
        self.assertIn(str(post), str(insight))


class WebsocketTests(TestCase):
    def test_anonymous_rejected(self):
        async def scenario():
            communicator = WebsocketCommunicator(NotificationConsumer.as_asgi(), "/ws/notifications/")
            communicator.scope["user"] = AnonymousUser()
            connected, _ = await communicator.connect()
            return connected

        self.assertFalse(async_to_sync(scenario)())

    def test_member_receives_pushed_notification(self):
        member = make_member("lina")

        async def scenario():
            communicator = WebsocketCommunicator(NotificationConsumer.as_asgi(), "/ws/notifications/")
            communicator.scope["user"] = member
            connected, _ = await communicator.connect()
            first = await communicator.receive_json_from()
            await get_channel_layer().group_send(
                group_name(member.pk), {"type": "notify", "payload": {"type": "notification", "title": "Test", "unread": 1}}
            )
            pushed = await communicator.receive_json_from()
            await communicator.disconnect()
            return connected, first, pushed

        connected, first, pushed = async_to_sync(scenario)()
        self.assertTrue(connected)
        self.assertEqual(first["type"], "unread")
        self.assertEqual(pushed["title"], "Test")
