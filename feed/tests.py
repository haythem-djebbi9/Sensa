from django.urls import reverse

from core.testing import SensaTestCase, make_member
from enrichment import pipeline
from enrichment.models import Tag
from publications.models import Audio, Publication, Republication
from social.models import Abonnement, Reaction

from . import services
from .ai import cosine, embed_text
from .models import Recherche


class FeedTestCase(SensaTestCase):
    def make_post(self, author, text, transcription=None, tags=()):
        post = Publication.objects.create(author=author, text=text)
        if transcription:
            Audio.objects.create(publication=post, file="publications/audio/test.wav", duration=3, transcription=transcription)
        with self.captureOnCommitCallbacks(execute=True):
            pipeline.launch(post, types=["tags", "moderation", "embedding"])
        for name in tags:
            post.tags.add(Tag.objects.get_or_create(name=name)[0])
        post.refresh_from_db()
        return post


class HomeFeedTests(FeedTestCase):
    def setUp(self):
        self.lina, self.maya, self.noah = make_member("lina"), make_member("maya"), make_member("noah")

    def test_visitor_sees_landing(self):
        response = self.client.get(reverse("feed:home"))
        self.assertTemplateUsed(response, "accounts/landing.html")

    def test_following_feed_is_chronological_and_filtered(self):
        old = self.make_post(self.maya, "Premier post de Maya")
        new = self.make_post(self.maya, "Second post de Maya")
        other = self.make_post(self.noah, "Post de Noah")
        Abonnement.objects.create(follower=self.lina, followed=self.maya)
        self.client.force_login(self.lina)
        posts = list(self.client.get(reverse("feed:home")).context["page"])
        self.assertEqual(posts, [new, old])
        self.assertNotIn(other, posts)
        self.assertIn(other, list(self.client.get(reverse("feed:home"), {"fil": "recent"}).context["page"]))

    def test_feed_without_follows_falls_back_to_recent(self):
        post = self.make_post(self.maya, "Bonjour tout le monde")
        self.client.force_login(self.lina)
        response = self.client.get(reverse("feed:home"))
        self.assertIn(post, list(response.context["page"]))
        self.assertFalse(response.context["follows_someone"])


class RepublicationFeedTests(FeedTestCase):
    def setUp(self):
        self.lina, self.maya, self.noah = make_member("lina"), make_member("maya"), make_member("noah")
        Abonnement.objects.create(follower=self.lina, followed=self.maya)
        self.noah_post = self.make_post(self.noah, "Coucher de soleil à Sidi Bou Saïd")
        self.maya_post = self.make_post(self.maya, "Mon café du matin")
        self.client.force_login(self.lina)

    def feed(self):
        return list(self.client.get(reverse("feed:home")).context["page"])

    def test_repost_by_followed_member_appears_on_top_with_label(self):
        Republication.objects.create(publication=self.noah_post, member=self.maya)
        posts = self.feed()
        self.assertEqual(posts, [self.noah_post, self.maya_post])
        self.assertEqual(posts[0].reposted_by_id, self.maya.pk)
        response = self.client.get(reverse("feed:home"))
        self.assertContains(response, "a republié")

    def test_post_listed_once_when_reposted_twice(self):
        from datetime import timedelta

        from django.utils import timezone

        Abonnement.objects.create(follower=self.lina, followed=self.noah)
        karim = make_member("karim")
        Abonnement.objects.create(follower=self.lina, followed=karim)
        Republication.objects.create(publication=self.noah_post, member=self.maya)
        latest = Republication.objects.create(publication=self.noah_post, member=karim)
        Republication.objects.filter(pk=latest.pk).update(created_at=timezone.now() + timedelta(minutes=1))
        posts = self.feed()
        self.assertEqual(posts.count(self.noah_post), 1)
        self.assertEqual(posts[0].reposted_by_id, karim.pk)

    def test_own_repost_labelled_for_me(self):
        Republication.objects.create(publication=self.noah_post, member=self.lina)
        self.assertContains(self.client.get(reverse("feed:home")), "Vous avez republié")

    def test_repost_by_stranger_not_shown(self):
        Republication.objects.create(publication=self.maya_post, member=make_member("karim"))
        self.assertNotIn(self.noah_post, self.feed())

    def test_blocked_original_disappears(self):
        Republication.objects.create(publication=self.noah_post, member=self.maya)
        Publication.objects.filter(pk=self.noah_post.pk).update(status=Publication.Status.BLOCKED)
        self.assertNotIn(self.noah_post, self.feed())


class SearchTests(FeedTestCase):
    def setUp(self):
        self.lina, self.maya = make_member("lina"), make_member("maya")
        self.audio_post = self.make_post(self.maya, "Petit vocal du matin", transcription="On entend les vagues et les mouettes sur le port")
        self.text_post = self.make_post(self.maya, "Recette du couscous au poisson #cuisine")
        self.client.force_login(self.lina)

    def test_keyword_search_finds_transcription(self):
        response = self.client.get(reverse("feed:search"), {"q": "mouettes"})
        results = response.context["results"]
        self.assertEqual(results, [self.audio_post])
        self.assertEqual(results[0].match, "Trouvé dans la transcription audio")
        self.assertEqual(Recherche.objects.get().results_count, 1)

    def test_hashtag_search(self):
        response = self.client.get(reverse("feed:search"), {"q": "#cuisine"})
        self.assertEqual(response.context["results"], [self.text_post])
        self.assertEqual(response.context["used_mode"], "tag")

    def test_type_filter(self):
        response = self.client.get(reverse("feed:search"), {"q": "matin", "type": "audio"})
        self.assertEqual(response.context["results"], [self.audio_post])
        response = self.client.get(reverse("feed:search"), {"q": "matin", "type": "images"})
        self.assertEqual(response.context["results"], [])

    def test_semantic_search(self):
        response = self.client.get(reverse("feed:search"), {"q": "poissons et recettes", "mode": "sens"})
        self.assertEqual(response.context["results"][0], self.text_post)

    def test_hidden_posts_not_searchable(self):
        Publication.objects.filter(pk=self.text_post.pk).update(status=Publication.Status.BLOCKED)
        response = self.client.get(reverse("feed:search"), {"q": "couscous"})
        self.assertNotIn(self.text_post, response.context["results"])

    def test_tag_page(self):
        response = self.client.get(reverse("feed:tag", args=["cuisine"]))
        self.assertIn(self.text_post, list(response.context["page"]))


class RecommendationTests(FeedTestCase):
    def test_recommendations_are_explained(self):
        lina, maya, noah = make_member("lina"), make_member("maya"), make_member("noah")
        liked = self.make_post(maya, "Randonnée en montagne au lever du soleil, paysage magnifique")
        similar = self.make_post(noah, "Randonnée en montagne : le soleil se lève sur le paysage")
        self.make_post(noah, "Résultats du match de football hier soir")
        Reaction.objects.create(publication=liked, member=lina)
        recommended = services.recommendations(lina)
        self.assertEqual(recommended[0], similar)
        self.assertIn("que vous avez aimé", recommended[0].reason)
        self.assertNotIn(liked, recommended)

    def test_cold_start_uses_popular_posts(self):
        lina, maya = make_member("lina"), make_member("maya")
        post = self.make_post(maya, "Un post populaire")
        recommended = services.recommendations(lina)
        self.assertEqual(recommended, [post])
        self.assertEqual(recommended[0].reason, "Populaire en ce moment")


class EmbeddingTests(SensaTestCase):
    def test_hash_embeddings_similarity(self):
        a = embed_text("Les mouettes survolent le port de pêche")
        b = embed_text("Une mouette au-dessus du port")
        c = embed_text("Résultat du match de football")
        self.assertGreater(cosine(a, b), cosine(a, c))
        self.assertAlmostEqual(cosine(a, a), 1.0, places=3)
