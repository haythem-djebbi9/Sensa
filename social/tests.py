from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse

from core.testing import SensaTestCase, make_member
from publications.models import Publication

from .ai import quick_tone
from .consumers import NotificationConsumer
from .models import Abonnement, Commentaire, Notification, Reaction
from .services import group_name


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
