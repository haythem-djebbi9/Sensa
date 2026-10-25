from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache as rate_cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.views import safe_next
from enrichment import cache, pipeline
from enrichment.models import AnalyseIA
from social.models import Notification
from social.services import annotate_social, notify
from social.views import CommentForm

from . import ai
from .forms import PublicationEditForm, PublicationForm, strip_image_metadata
from .models import Enregistrement, Image, Publication, Republication
from .validators import validate_image_upload


def posts_last_hour(user):
    return Publication.objects.filter(author=user, created_at__gte=timezone.now() - timedelta(hours=1)).count()


@login_required
def create(request):
    """Formulaire de publication : texte + image + audio, publié immédiatement (PENDING)."""
    used = posts_last_hour(request.user)
    if request.method == "POST" and used >= settings.SENSA_POSTS_PER_HOUR:
        return render(request, "publications/rate_limited.html", {"limit": settings.SENSA_POSTS_PER_HOUR}, status=429)
    form = PublicationForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        publication = form.save_for(request.user)
        pipeline.launch(publication)
        messages.success(request, "C'est en ligne ! L'IA enrichit votre publication en arrière-plan.")
        return redirect("feed:home")
    return render(
        request,
        "publications/create.html",
        {"form": form, "remaining": max(settings.SENSA_POSTS_PER_HOUR - used, 0), "limit": settings.SENSA_POSTS_PER_HOUR},
    )


def render_detail(request, publication, *, comment_form=None, be_kind=False, status=200):
    post = annotate_social(Publication.objects.filter(pk=publication.pk).for_cards(), request.user).get()
    comments = post.commentaires.select_related("author", "author__profile")
    show_pipeline = request.user.pk == post.author_id or request.user.is_moderator
    context = {
        "post": post,
        "comments": comments,
        "comment_form": comment_form or CommentForm(),
        "be_kind": be_kind,
        "analyses": post.analyses.exclude(type__in=AnalyseIA.SIDE_TYPES).order_by("created_at") if show_pipeline else None,
        "translation_languages": [("fr", "Français"), ("en", "Anglais"), ("ar", "Arabe")],
    }
    return render(request, "publications/detail.html", context, status=status)


@login_required
def detail(request, pk):
    publication = get_object_or_404(Publication.objects.visible_to(request.user), pk=pk)
    return render_detail(request, publication)


@login_required
def card(request, pk):
    """Carte HTML d'un post (rafraîchie en JavaScript quand l'analyse IA se termine)."""
    get_object_or_404(Publication.objects.visible_to(request.user), pk=pk)
    post = annotate_social(Publication.objects.filter(pk=pk).for_cards(), request.user).get()
    response = render(request, "partials/post_card.html", {"post": post, "card_mode": True})
    response["X-Post-Status"] = post.status
    return response


@login_required
def edit(request, pk):
    publication = get_object_or_404(Publication.objects.for_cards(), pk=pk)
    if not publication.can_edit(request.user):
        raise PermissionDenied("Seul l'auteur peut modifier cette publication.")
    form = PublicationEditForm(request.POST or None, request.FILES or None, instance=publication)
    if request.method == "POST" and form.is_valid():
        text_changed = "text" in form.changed_data
        form.save()
        if form.media_changed:
            # Nouveaux médias : toute l'analyse est refaite (descriptions, transcription, résumé, modération…).
            pipeline.launch(Publication.objects.get(pk=publication.pk))
            messages.success(request, "Publication modifiée. L'IA analyse les nouveaux médias.")
        elif text_changed:
            # Le texte a changé : tags, modération et embedding sont recalculés.
            pipeline.launch(publication, types=[AnalyseIA.Type.TAGS, AnalyseIA.Type.MODERATION, AnalyseIA.Type.EMBEDDING])
            messages.success(request, "Publication modifiée. L'IA réanalyse le nouveau texte.")
        else:
            messages.success(request, "Publication modifiée.")
        return redirect(publication)
    return render(request, "publications/edit.html", {"form": form, "post": publication})


@login_required
def delete(request, pk):
    publication = get_object_or_404(Publication.objects.select_related("author"), pk=pk)
    if not publication.can_delete(request.user):
        raise PermissionDenied("Vous ne pouvez pas supprimer cette publication.")
    if request.method == "POST":
        publication.delete()
        messages.success(request, "Publication supprimée.")
        return redirect(safe_next(request, reverse("feed:home")))
    return render(request, "publications/confirm_delete.html", {"post": publication})


@login_required
def retry(request, pk):
    """Relancer l'analyse IA d'une publication en échec (auteur ou modérateur)."""
    publication = get_object_or_404(Publication, pk=pk)
    if request.method != "POST":
        return redirect(publication)
    if not (publication.can_edit(request.user) or request.user.is_moderator):
        raise PermissionDenied
    count = pipeline.retry_failed(publication)
    if count:
        messages.success(request, f"Analyse relancée ({count} étape(s)).")
    else:
        messages.info(request, "Aucune analyse en échec à relancer.")
    return redirect(safe_next(request, publication.get_absolute_url()))


@login_required
@require_POST
def republish_toggle(request, pk):
    """Republier un post (ou annuler la republication), comme sur Instagram."""
    publication = get_object_or_404(Publication.objects.visible_to(request.user).select_related("author"), pk=pk)
    existing = Republication.objects.filter(publication=publication, member=request.user)
    if existing.exists():
        existing.delete()
        republished = False
    elif publication.can_republish(request.user):
        Republication.objects.get_or_create(publication=publication, member=request.user)
        republished = True
        already_notified = Notification.objects.filter(
            recipient=publication.author, actor=request.user, type=Notification.Type.REPUBLISH, publication=publication
        ).exists()
        if not already_notified:
            notify(publication.author, Notification.Type.REPUBLISH, actor=request.user, publication=publication,
                   message=publication.excerpt)
    else:
        raise PermissionDenied("Seules les publications publiques des autres membres peuvent être republiées.")
    count = publication.republications.count()
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"republished": republished, "count": count})
    return redirect(safe_next(request, publication.get_absolute_url()) + f"#post-{publication.pk}")


@login_required
@require_POST
def save_toggle(request, pk):
    """Enregistrer un post pour plus tard (ou le retirer des enregistrements)."""
    publication = get_object_or_404(Publication.objects.visible_to(request.user), pk=pk)
    deleted, _ = Enregistrement.objects.filter(publication=publication, member=request.user).delete()
    saved = not deleted
    if saved:
        Enregistrement.objects.get_or_create(publication=publication, member=request.user)
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"saved": saved})
    return redirect(safe_next(request, publication.get_absolute_url()) + f"#post-{publication.pk}")


@login_required
@require_POST
def suggest_alt_text(request):
    """Propose un texte alternatif pour l'image choisie dans le formulaire, avant publication."""
    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"error": "Aucune image reçue."}, status=400)
    counter = f"suggestions-texte-alternatif:{request.user.pk}"
    rate_cache.add(counter, 0, timeout=3600)
    if rate_cache.incr(counter) > settings.SENSA_ALT_SUGGESTIONS_PER_HOUR:
        return JsonResponse({"error": "Trop de suggestions demandées : réessayez dans une heure."}, status=429)
    try:
        validate_image_upload(upload)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=400)
    # Mêmes octets que l'image qui sera publiée : la description mise en cache ici
    # est réutilisée par le pipeline, l'image n'est donc analysée qu'une fois.
    data = strip_image_metadata(upload).read()
    key = cache.make_key(AnalyseIA.Type.DESCRIPTION, ContentFile(data))
    hit = cache.lookup(key)
    if hit is not None:
        description, provider = hit.result.get("description", ""), hit.provider
    else:
        description, provider = ai.describe_image_data(data)
        if provider != "description-basique":
            cache.store(key, AnalyseIA.Type.DESCRIPTION, {"description": description}, provider)
    alt_text = ai.shorten(description, Image._meta.get_field("alt_text").max_length)
    return JsonResponse({"alt_text": alt_text, "approximate": provider == "description-basique"})
