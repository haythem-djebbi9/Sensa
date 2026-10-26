"""Fil chronologique, recherche plein texte / sémantique et recommandations (module 5)."""

import re
from datetime import timedelta

from django.db import connection
from django.db.models import Count, Exists, OuterRef, Q, Subquery, UUIDField
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from core.ai.text import STOPWORDS, normalize, slug_tag
from enrichment.models import Tag
from publications.models import Audio, Image, Publication, Republication
from social.models import Abonnement, Reaction
from social.services import annotate_social

from .ai import centroid, cosine, embed_text, semantic_threshold
from .models import Recherche

KINDS = [("tout", "Tout"), ("texte", "Texte"), ("images", "Images"), ("audio", "Audio")]


def cards(user, queryset=None):
    queryset = queryset if queryset is not None else Publication.objects.all()
    return annotate_social(queryset.visible_to(user).for_cards(), user)


def _ordered(user, ids, extra=None):
    """Recharge des publications annotées en conservant l'ordre `ids`."""
    posts = {post.pk: post for post in cards(user, Publication.objects.filter(pk__in=ids))}
    result = []
    for pk in ids:
        post = posts.get(pk)
        if post is not None:
            for key, value in (extra or {}).get(pk, {}).items():
                setattr(post, key, value)
            result.append(post)
    return result


# --- Fils -----------------------------------------------------------------------

def following_feed(user):
    """Posts des comptes suivis et les siens, plus ceux qu'ils ont republiés.

    Un post republié remonte à la date de sa dernière republication, une seule fois.
    """
    followed = Abonnement.objects.filter(follower=user).values("followed_id")
    reposts = Republication.objects.filter(
        Q(member_id__in=followed) | Q(member=user), publication=OuterRef("pk")
    ).order_by("-created_at")
    return (
        cards(user)
        .annotate(
            reposted_at=Subquery(reposts.values("created_at")[:1]),
            reposted_by_id=Subquery(reposts.values("member_id")[:1], output_field=UUIDField()),
            reposted_by_name=Subquery(reposts.values("member__profile__display_name")[:1]),
            reposted_by_username=Subquery(reposts.values("member__username")[:1]),
        )
        .filter(Q(author_id__in=followed) | Q(author=user) | Q(reposted_at__isnull=False))
        .annotate(activity_at=Greatest("created_at", Coalesce("reposted_at", "created_at")))
        .order_by("-activity_at")
    )


def latest_feed(user):
    return cards(user).order_by("-created_at")


def suggested_members(user, limit=4):
    from accounts.models import Membre

    followed = Abonnement.objects.filter(follower=user).values("followed_id")
    return (
        Membre.objects.filter(is_active=True)
        .exclude(pk=user.pk)
        .exclude(pk__in=followed)
        .select_related("profile")
        .annotate(nb_followers=Count("followers"), nb_posts=Count("publications"))
        .filter(nb_posts__gt=0)
        .order_by("-nb_followers", "-nb_posts")[:limit]
    )


def recommendations(user, limit=30):
    """Posts recommandés : plus proches voisins de ce que le membre a aimé, avec explication.

    Pour éviter la bulle de filtre, une publication sur cinq vient d'un auteur
    que le membre ne suit pas encore (« Pour élargir vos horizons »).
    """
    liked_ids = list(
        Reaction.objects.filter(member=user).order_by("-created_at").values_list("publication_id", flat=True)[:50]
    )
    liked = list(Publication.objects.filter(pk__in=liked_ids, embedding__isnull=False).only("id", "text", "embedding"))
    candidates = list(
        Publication.objects.visible_to(user)
        .filter(status=Publication.Status.READY, embedding__isnull=False)
        .exclude(author=user)
        .exclude(pk__in=liked_ids)
        .order_by("-created_at")
        .only("id", "author_id", "embedding", "created_at")[:500]
    )
    now = timezone.now()
    scored, reasons = [], {}
    profile_vector = centroid([post.embedding for post in liked])
    if profile_vector is not None:
        for candidate in candidates:
            best_post, best_sim = None, 0.0
            for post in liked:
                similarity = cosine(candidate.embedding, post.embedding)
                if similarity > best_sim:
                    best_post, best_sim = post, similarity
            age_days = (now - candidate.created_at).total_seconds() / 86400
            score = 0.6 * cosine(candidate.embedding, profile_vector) + 0.4 * best_sim + 0.05 / (1 + age_days)
            if best_post is not None and best_sim >= semantic_threshold(candidate.embedding.get("model")):
                excerpt = best_post.text.strip().replace("\n", " ")[:50] or "une publication"
                reasons[candidate.pk] = {"reason": f"Proche de « {excerpt} » que vous avez aimé", "reason_icon": "heart"}
                scored.append((score, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [candidate.pk for _, candidate in scored[:limit]]

    if len(ranked) < limit:
        # Démarrage à froid : publications populaires des 14 derniers jours.
        popular = (
            Publication.objects.visible_to(user)
            .filter(status=Publication.Status.READY, created_at__gte=now - timedelta(days=14))
            .exclude(author=user)
            .exclude(pk__in=liked_ids + ranked)
            .annotate(nb_likes=Count("reactions"))
            .order_by("-nb_likes", "-created_at")
            .values_list("pk", flat=True)[: limit - len(ranked)]
        )
        for pk in popular:
            ranked.append(pk)
            reasons.setdefault(pk, {"reason": "Populaire en ce moment", "reason_icon": "activity"})

    followed = set(Abonnement.objects.filter(follower=user).values_list("followed_id", flat=True))
    discovery = [
        candidate.pk
        for candidate in candidates
        if candidate.author_id not in followed and candidate.pk not in ranked
    ][: max(1, limit // 5)]
    mixed = []
    for index, pk in enumerate(ranked):
        if index and index % 4 == 0 and discovery:
            extra = discovery.pop(0)
            mixed.append(extra)
            reasons[extra] = {"reason": "Pour élargir vos horizons", "reason_icon": "globe"}
        mixed.append(pk)
    return _ordered(user, mixed[:limit], reasons)


# --- Recherche ------------------------------------------------------------------

def _filter_kind(queryset, kind):
    if kind == "texte":
        return queryset.exclude(text="")
    if kind == "images":
        return queryset.filter(Exists(Image.objects.filter(publication=OuterRef("pk"))))
    if kind == "audio":
        return queryset.filter(Exists(Audio.objects.filter(publication=OuterRef("pk"))))
    return queryset


def _query_terms(query):
    terms = []
    for raw in query.replace("#", " ").split():
        word = raw.strip(".,;:!?«»\"'()").lower()
        if len(word) >= 2 and normalize(word) not in STOPWORDS:
            terms.append(word)
    return terms or [query.strip().lower()]


def _keyword_ids(query):
    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import SearchQuery, SearchVector

        vector = (
            SearchVector("text", weight="A", config="french")
            + SearchVector("audio__transcription", weight="B", config="french")
            + SearchVector("tags__name", weight="B", config="simple")
            + SearchVector("images__description_ia", weight="C", config="french")
        )
        return (
            Publication.objects.annotate(document=vector)
            .filter(document=SearchQuery(query, config="french", search_type="websearch"))
            .values("pk")
        )
    condition = Q()
    for term in _query_terms(query):
        condition &= (
            Q(text__icontains=term)
            | Q(audio__transcription__icontains=term)
            | Q(tags__name__icontains=term)
            | Q(images__description_ia__icontains=term)
        )
    return Publication.objects.filter(condition).values("pk")


def _match_source(post, terms):
    """Où la requête a-t-elle été trouvée ? (score, libellé affiché)."""
    def contains(value):
        value = normalize(value or "")
        return sum(1 for term in terms if normalize(term) in value)

    audio = post.audio_or_none
    sources = [
        (contains(post.text) * 3, "Trouvé dans le texte"),
        (contains(audio.transcription if audio else "") * 2, "Trouvé dans la transcription audio"),
        (contains(" ".join(tag.name for tag in post.tags.all())) * 2, "Trouvé dans les tags"),
        (contains(" ".join(image.description_ia for image in post.images.all())), "Trouvé dans la description de l'image"),
    ]
    score = sum(value for value, _ in sources)
    best = max(sources, key=lambda item: item[0])
    return score, best[1] if best[0] else "Correspondance approchée"


def keyword_search(user, query, kind="tout", limit=50):
    queryset = _filter_kind(cards(user), kind).filter(pk__in=_keyword_ids(query))
    posts = list(queryset.order_by("-created_at")[:200])
    terms = _query_terms(query)
    ranked = []
    for post in posts:
        score, label = _match_source(post, terms)
        post.match = label
        post.match_icon = "spark" if "transcription" in label or "description" in label else "search"
        ranked.append((score, post.created_at, post))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [post for _, _, post in ranked[:limit]]


def semantic_search(user, query, kind="tout", limit=30):
    query_vector = embed_text(query)
    threshold = semantic_threshold(query_vector["model"])
    candidates = list(
        _filter_kind(Publication.objects.visible_to(user), kind)
        .filter(embedding__isnull=False)
        .order_by("-created_at")
        .only("id", "embedding")[:800]
    )
    scored = sorted(
        ((cosine(query_vector, candidate.embedding), candidate.pk) for candidate in candidates),
        reverse=True,
    )
    scored = [(similarity, pk) for similarity, pk in scored if similarity >= threshold][:limit]
    extra = {
        pk: {"match": f"Proche par le sens ({round(similarity * 100)} %)", "match_icon": "spark"}
        for similarity, pk in scored
    }
    return _ordered(user, [pk for _, pk in scored], extra)


def hashtag_search(user, tag_name, kind="tout"):
    tag_name = slug_tag(tag_name)
    posts = list(_filter_kind(cards(user), kind).filter(tags__name=tag_name).order_by("-created_at")[:50])
    # Prefetch tags for display
    from publications.models import Publication
    post_ids = [p.pk for p in posts]
    if post_ids:
        posts = list(cards(user, Publication.objects.filter(pk__in=post_ids)).order_by("-created_at"))
    for post in posts:
        post.match = f"Tag #{tag_name}"
        post.match_icon = "hash"
    return posts


def search(user, query, kind="tout", mode="mots"):
    """Renvoie (résultats, mode réellement utilisé, message d'information)."""
    query = (query or "").strip()[:200]
    notice = ""
    # Nettoyage : ignorer les requêtes ne contenant que des symboles
    cleaned = re.sub(r"[#\s\.,;:!?«»\"'()\-_/\\]+", " ", query).strip()
    if not cleaned:
        return [], mode, ""
    # Si la requête est majoritairement des hashtags, normaliser
    if query.lstrip().startswith("#") and len(query.lstrip()) > 1:
        slug = slug_tag(query)
        if not slug:
            return [], mode, ""
        mode = Recherche.Mode.HASHTAG
        results = hashtag_search(user, slug, kind)
    elif mode == Recherche.Mode.SEMANTIC:
        results = semantic_search(user, query, kind)
    else:
        mode = Recherche.Mode.KEYWORDS
        results = keyword_search(user, query, kind)
        if not results:
            results = semantic_search(user, query, kind)
            if results:
                mode = Recherche.Mode.SEMANTIC
                notice = "Aucun résultat exact : voici des publications proches par le sens."
    Recherche.objects.create(member=user, query=query, mode=mode, results_count=len(results))
    return results, mode, notice


from django.core.cache import cache

def trending_tags(limit=10, days=7):
    cache_key = f"feed:trending_tags:{limit}:{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    since = timezone.now() - timedelta(days=days)
    result = list(
        Tag.objects.annotate(
            nb=Count(
                "publications",
                filter=Q(publications__created_at__gte=since, publications__status=Publication.Status.READY),
            )
        )
        .filter(nb__gt=0)
        .order_by("-nb", "name")[:limit]
    )
    cache.set(cache_key, result, 300)
    return result


def recent_searches(user, limit=5):
    seen, result = set(), []
    for query in Recherche.objects.filter(member=user).values_list("query", flat=True)[:30]:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            result.append(query)
        if len(result) >= limit:
            break
    return result
