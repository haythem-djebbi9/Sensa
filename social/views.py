from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Membre
from accounts.views import safe_next
from enrichment.dispatch import enqueue
from publications.models import Publication

from .ai import quick_tone
from .models import Abonnement, Commentaire, Notification, Reaction
from .services import notify
from .tasks import analyse_tone


class CommentForm(forms.Form):
    text = forms.CharField(
        label="Commentaire",
        max_length=1000,
        widget=forms.TextInput(attrs={"placeholder": "Ajoutez votre voix à la conversation…", "autocomplete": "off"}),
    )


def _wants_json(request):
    return request.headers.get("X-Requested-With") == "fetch"


@login_required
@require_POST
def follow_toggle(request, username):
    target = get_object_or_404(Membre, username__iexact=username)
    if target == request.user:
        messages.error(request, "Vous ne pouvez pas vous suivre vous-même.")
        return redirect(target)
    existing = Abonnement.objects.filter(follower=request.user, followed=target)
    if existing.exists():
        existing.delete()
        following = False
    else:
        Abonnement.objects.create(follower=request.user, followed=target)
        notify(target, Notification.Type.FOLLOW, actor=request.user)
        following = True
    if _wants_json(request):
        return JsonResponse({"following": following, "followers": target.followers.count()})
    return redirect(safe_next(request, target.get_absolute_url()))


@login_required
@require_POST
def like_toggle(request, pk):
    publication = get_object_or_404(Publication.objects.visible_to(request.user).select_related("author"), pk=pk)
    existing = Reaction.objects.filter(publication=publication, member=request.user, type=Reaction.Type.LIKE)
    if existing.exists():
        existing.delete()
        liked = False
    else:
        Reaction.objects.create(publication=publication, member=request.user, type=Reaction.Type.LIKE)
        liked = True
        already_notified = Notification.objects.filter(
            recipient=publication.author, actor=request.user, type=Notification.Type.LIKE, publication=publication
        ).exists()
        if not already_notified:
            notify(publication.author, Notification.Type.LIKE, actor=request.user, publication=publication,
                   message=publication.excerpt)
    count = publication.reactions.count()
    if _wants_json(request):
        return JsonResponse({"liked": liked, "count": count})
    return redirect(safe_next(request, publication.get_absolute_url()) + f"#post-{publication.pk}")


@login_required
@require_POST
def comment_create(request, pk):
    from publications.views import render_detail

    publication = get_object_or_404(Publication.objects.visible_to(request.user).select_related("author"), pk=pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        return render_detail(request, publication, comment_form=form, status=400)
    text = form.cleaned_data["text"].strip()
    tone, _ = quick_tone(text)
    if tone == "aggressive" and not request.POST.get("confirm"):
        # Avertissement « soyez bienveillant » avant publication.
        return render_detail(request, publication, comment_form=form, be_kind=True)
    with transaction.atomic():
        comment = Commentaire.objects.create(publication=publication, author=request.user, text=text)
        notify(publication.author, Notification.Type.COMMENT, actor=request.user, publication=publication,
               commentaire=comment, message=text[:140])
        transaction.on_commit(lambda: enqueue(analyse_tone, [comment.pk]))
    return redirect(f"{publication.get_absolute_url()}#comment-{comment.pk}")


@login_required
@require_POST
def comment_delete(request, pk):
    comment = get_object_or_404(Commentaire.objects.select_related("publication"), pk=pk)
    if not comment.can_delete(request.user):
        messages.error(request, "Vous ne pouvez pas supprimer ce commentaire.")
    else:
        comment.delete()
        messages.success(request, "Commentaire supprimé.")
    return redirect(f"{comment.publication.get_absolute_url()}#commentaires")


@login_required
def notifications(request):
    queryset = request.user.notifications.select_related("actor", "actor__profile", "publication").order_by(
        "is_read", "-priority", "-created_at"
    )
    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    return render(request, "social/notifications.html", {"page": page})


@login_required
@require_POST
def notifications_mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    if _wants_json(request):
        return JsonResponse({"unread": 0})
    messages.success(request, "Toutes les notifications sont marquées comme lues.")
    return redirect("social:notifications")


@login_required
def notification_open(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    if notification.publication_id:
        url = notification.publication.get_absolute_url()
        if notification.commentaire_id:
            url += f"#comment-{notification.commentaire_id}"
        return redirect(url)
    if notification.actor_id:
        return redirect(notification.actor)
    return redirect("social:notifications")


@login_required
def notifications_count(request):
    return JsonResponse({"unread": request.user.notifications.filter(is_read=False).count()})


@login_required
def follow_list(request, username, which):
    member = get_object_or_404(Membre.objects.select_related("profile"), username__iexact=username)
    if which == "abonnes":
        people = Membre.objects.filter(following__followed=member)
        title = f"Abonnés de {member.display_name}"
    else:
        people = Membre.objects.filter(followers__follower=member)
        title = f"Abonnements de {member.display_name}"
    people = people.select_related("profile").order_by("username")
    my_following = set(Abonnement.objects.filter(follower=request.user).values_list("followed_id", flat=True))
    page = Paginator(people, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "social/follow_list.html",
        {"member": member, "page": page, "title": title, "which": which, "my_following": my_following},
    )
