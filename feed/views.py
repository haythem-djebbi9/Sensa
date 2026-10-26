from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from core.ai.text import slug_tag
from enrichment.models import Tag
from social.models import Abonnement

from . import services
from .models import Recherche

FEED_TABS = [
    ("abonnements", "Abonnements"),
    ("pour-vous", "Pour vous"),
    ("recent", "Récent"),
]

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


def _greeting(user):
    now = timezone.localtime()
    hello = "Bonsoir" if now.hour >= 18 or now.hour < 5 else "Bonjour"
    return f"{DAYS[now.weekday()].capitalize()} {now.day} {MONTHS[now.month - 1]}", f"{hello}, {user.display_name}"


def home(request):
    """Accueil : page de présentation pour les visiteurs, fil pour les membres."""
    if not request.user.is_authenticated:
        return render(request, "accounts/landing.html")

    tab = request.GET.get("fil", "abonnements")
    if tab not in dict(FEED_TABS):
        tab = "abonnements"
    follows_someone = Abonnement.objects.filter(follower=request.user).exists()
    if tab == "pour-vous":
        posts = services.recommendations(request.user, limit=40)
    elif tab == "recent" or not follows_someone:
        posts = services.latest_feed(request.user)
    else:
        posts = services.following_feed(request.user)
    page = Paginator(posts, settings.SENSA_FEED_PAGE_SIZE).get_page(request.GET.get("page"))
    date_label, greeting = _greeting(request.user)
    context = {
        "page": page,
        "tab": tab,
        "tabs": FEED_TABS,
        "follows_someone": follows_someone,
        "date_label": date_label,
        "greeting": greeting,
        "suggestions": services.suggested_members(request.user) if tab != "pour-vous" else [],
    }
    return render(request, "feed/home.html", context)


@login_required
def search(request):
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("type", "tout")
    if kind not in dict(services.KINDS):
        kind = "tout"
    mode = request.GET.get("mode", Recherche.Mode.KEYWORDS)
    if mode not in (Recherche.Mode.KEYWORDS, Recherche.Mode.SEMANTIC):
        mode = Recherche.Mode.KEYWORDS
    results, used_mode, notice = services.search(request.user, query, kind, mode)
    page = Paginator(results, settings.SENSA_FEED_PAGE_SIZE).get_page(request.GET.get("page"))
    context = {
        "query": query,
        "kind": kind,
        "kinds": services.KINDS,
        "mode": mode,
        "used_mode": used_mode,
        "notice": notice,
        "results": results,
        "page": page,
        "trending": services.trending_tags(),
        "recent": services.recent_searches(request.user) if not query else [],
    }
    return render(request, "feed/search.html", context)


from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect


@login_required
def clear_searches(request):
    if request.method == "POST":
        Recherche.objects.filter(member=request.user).delete()
        messages.success(request, "Recherches récentes effacées.")
    return redirect("feed:search")


@login_required
def delete_search(request, pk):
    if request.method == "POST":
        recherche = get_object_or_404(Recherche, pk=pk, member=request.user)
        recherche.delete()
        messages.success(request, "Recherche supprimée.")
    return redirect("feed:search")


@login_required
def delete_search(request, pk):
    if request.method == "POST":
        recherche = get_object_or_404(Recherche, pk=pk, member=request.user)
        recherche.delete()
        messages.success(request, "Recherche supprimée.")
    return redirect("feed:search")


@login_required
def tag(request, name):
    name = slug_tag(name).strip()
    if not name:
        from django.shortcuts import redirect
        return redirect("feed:search")
    tag_obj = Tag.objects.filter(name=name).first()
    posts = services.cards(request.user).filter(tags__name=name).order_by("-created_at") if tag_obj else []
    page = Paginator(posts, settings.SENSA_FEED_PAGE_SIZE).get_page(request.GET.get("page"))
    suggestions = []
    if not tag_obj and not posts:
        suggestions = services.trending_tags(limit=6)
    return render(request, "feed/tag.html", {"tag_name": name, "tag": tag_obj, "page": page, "suggestions": suggestions})
