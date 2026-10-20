import statistics
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, FloatField, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Membre
from accounts.permissions import admin_required, moderator_required
from accounts.views import safe_next
from enrichment.models import AnalyseIA
from publications.models import Publication
from social.models import Notification
from social.services import notify

from .forms import AccountUpdateForm, ConfigurationIAForm, ReportForm
from .health import system_health
from .models import ConfigurationIA, DecisionModeration, Signalement

QUEUE_FILTERS = [
    ("a-traiter", "À traiter"),
    ("signales", "Signalés"),
    ("en-revue", "En revue"),
    ("bloques", "Bloqués"),
    ("echecs", "Échecs IA"),
]


def _queue_queryset():
    score = AnalyseIA.objects.filter(
        publication=OuterRef("pk"), type=AnalyseIA.Type.MODERATION, status=AnalyseIA.Status.DONE
    ).values("score")[:1]
    return (
        Publication.objects.for_cards()
        .annotate(
            ai_score=Coalesce(Subquery(score, output_field=FloatField()), Value(0.0)),
            open_reports=Count("signalements", filter=Q(signalements__status=Signalement.Status.OPEN), distinct=True),
        )
        .prefetch_related(
            Prefetch(
                "signalements",
                queryset=Signalement.objects.filter(status=Signalement.Status.OPEN).select_related("reporter"),
                to_attr="open_signalements",
            ),
            Prefetch(
                "analyses",
                queryset=AnalyseIA.objects.filter(type=AnalyseIA.Type.MODERATION),
                to_attr="moderation_rows",
            ),
        )
    )


def _filter_queue(queryset, key):
    if key == "signales":
        return queryset.filter(open_reports__gt=0)
    if key == "en-revue":
        return queryset.filter(status=Publication.Status.UNDER_REVIEW)
    if key == "bloques":
        return queryset.filter(status=Publication.Status.BLOCKED)
    if key == "echecs":
        return queryset.filter(status=Publication.Status.FAILED)
    return queryset.filter(Q(status=Publication.Status.UNDER_REVIEW) | Q(open_reports__gt=0))


@moderator_required
def queue(request):
    """File de modération : posts en revue et signalés, les plus risqués d'abord."""
    config = ConfigurationIA.get_solo()
    key = request.GET.get("filtre", "a-traiter")
    if key not in dict(QUEUE_FILTERS):
        key = "a-traiter"
    base = _queue_queryset()
    items = _filter_queue(base, key).order_by(F("ai_score").desc(), F("open_reports").desc(), "created_at")
    page = Paginator(items, 20).get_page(request.GET.get("page"))
    for item in page:
        row = item.moderation_rows[0] if item.moderation_rows else None
        item.ai_labels = (row.result or {}).get("labels", []) if row else []
        item.ai_justification = (row.result or {}).get("justification", "") if row else ""
        item.report_reasons = sorted({report.get_reason_display() for report in item.open_signalements})

    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    decisions_today = DecisionModeration.objects.filter(created_at__gte=today).count()
    decisions_yesterday = DecisionModeration.objects.filter(
        created_at__gte=today - timedelta(days=1), created_at__lt=today
    ).count()
    waits = list(
        DecisionModeration.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)).values_list(
            "waited_seconds", flat=True
        )
    )
    awaiting = _filter_queue(base, "a-traiter")
    context = {
        "page": page,
        "filters": [(k, label, _filter_queue(base, k).count()) for k, label in QUEUE_FILTERS],
        "current_filter": key,
        "config": config,
        "metrics": {
            "awaiting": awaiting.count(),
            "high_priority": awaiting.filter(Q(ai_score__gte=config.threshold_block) | Q(open_reports__gte=3)).count(),
            "today": decisions_today,
            "yesterday": decisions_yesterday,
            "median_wait": int(statistics.median(waits)) if waits else None,
        },
    }
    return render(request, "moderation/queue.html", context)


@moderator_required
@require_POST
def decide(request, pk):
    publication = get_object_or_404(Publication.objects.select_related("author"), pk=pk)
    action = request.POST.get("action")
    if action not in {"approve", "block", "dismiss"}:
        messages.error(request, "Action inconnue.")
        return redirect(safe_next(request, reverse("moderation:queue")))

    now = timezone.now()
    open_reports = publication.signalements.filter(status=Signalement.Status.OPEN)
    oldest_report = open_reports.order_by("created_at").first()
    waited_from = oldest_report.created_at if oldest_report else publication.created_at
    previous = publication.status

    with transaction.atomic():
        if action == "dismiss":
            open_reports.update(status=Signalement.Status.DISMISSED, handled_by=request.user, handled_at=now)
            log_action = DecisionModeration.Action.DISMISS
        else:
            publication.status = Publication.Status.READY if action == "approve" else Publication.Status.BLOCKED
            publication.save(update_fields=["status", "updated_at"])
            open_reports.update(status=Signalement.Status.RESOLVED, handled_by=request.user, handled_at=now)
            log_action = DecisionModeration.Action.APPROVE if action == "approve" else DecisionModeration.Action.BLOCK
        DecisionModeration.objects.create(
            publication=publication,
            moderator=request.user,
            action=log_action,
            previous_status=previous,
            note=request.POST.get("note", "")[:255],
            waited_seconds=max(0, int((now - waited_from).total_seconds())),
        )

    if action == "approve" and previous != Publication.Status.READY:
        notify(publication.author, Notification.Type.MODERATION, actor=request.user, publication=publication,
               message="Un modérateur a validé votre publication : elle est de nouveau visible.")
        messages.success(request, "Publication approuvée et visible.")
    elif action == "block" and previous != Publication.Status.BLOCKED:
        notify(publication.author, Notification.Type.MODERATION, actor=request.user, publication=publication,
               message="Un modérateur a bloqué votre publication. Elle n'est plus visible que par vous.")
        messages.success(request, "Publication bloquée.")
    else:
        messages.success(request, "Décision enregistrée.")
    return redirect(safe_next(request, reverse("moderation:queue")))


@login_required
def report(request, pk):
    publication = get_object_or_404(Publication.objects.visible_to(request.user).select_related("author"), pk=pk)
    if publication.author_id == request.user.pk:
        messages.info(request, "Vous ne pouvez pas signaler votre propre publication.")
        return redirect(publication)
    already = Signalement.objects.filter(
        publication=publication, reporter=request.user, status=Signalement.Status.OPEN
    ).exists()
    form = ReportForm(request.POST or None)
    if request.method == "POST" and not already and form.is_valid():
        signalement = form.save(commit=False)
        signalement.publication = publication
        signalement.reporter = request.user
        signalement.save()
        open_count = publication.signalements.filter(status=Signalement.Status.OPEN).count()
        if open_count >= settings.SENSA_REPORTS_BEFORE_REVIEW and publication.is_public:
            # Trop de signalements : la publication est masquée en attendant un modérateur.
            Publication.objects.filter(pk=publication.pk).update(status=Publication.Status.UNDER_REVIEW)
        messages.success(request, "Merci. Un modérateur va examiner cette publication.")
        return redirect("feed:home")
    return render(request, "moderation/report.html", {"publication": publication, "form": form, "already": already})


@admin_required
def admin_settings(request):
    """Paramètres système : rôles, seuils IA et santé du système."""
    config = ConfigurationIA.get_solo()
    form = ConfigurationIAForm(request.POST or None, config=config)
    if request.method == "POST" and form.is_valid():
        form.save(request.user)
        messages.success(request, "Paramètres de l'IA enregistrés.")
        return redirect("moderation:settings")
    staff = Membre.objects.select_related("profile").filter(
        Q(role__in=[Membre.Role.MODERATEUR, Membre.Role.ADMIN]) | Q(is_superuser=True)
    )
    recent = Membre.objects.select_related("profile").filter(role=Membre.Role.MEMBRE, is_superuser=False).order_by(
        "-date_joined"
    )[:5]
    context = {
        "form": form,
        "config": config,
        "staff": staff,
        "recent_members": recent,
        "roles": Membre.Role.choices,
        "health": system_health(),
    }
    return render(request, "moderation/settings.html", context)


@admin_required
def accounts(request):
    members = Membre.objects.select_related("profile").annotate(posts=Count("publications")).order_by("-date_joined")
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    if query:
        members = members.filter(
            Q(username__icontains=query) | Q(email__icontains=query) | Q(profile__display_name__icontains=query)
        )
    if role in Membre.Role.values:
        members = members.filter(role=role)
    page = Paginator(members, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "moderation/accounts.html",
        {"page": page, "query": query, "role": role, "roles": Membre.Role.choices},
    )


@admin_required
@require_POST
def account_update(request, pk):
    member = get_object_or_404(Membre, pk=pk)
    back = safe_next(request, reverse("moderation:accounts"))
    if member == request.user:
        messages.error(request, "Vous ne pouvez pas modifier votre propre rôle ni désactiver votre compte.")
        return redirect(back)
    data = {"role": request.POST.get("role", member.role), "is_active": request.POST.get("is_active", "on" if member.is_active else "") == "on"}
    form = AccountUpdateForm(data, instance=member)
    if form.is_valid():
        if member.is_superuser and form.cleaned_data["role"] != Membre.Role.ADMIN:
            messages.error(request, "Un super-utilisateur reste administrateur.")
        else:
            form.save()
            messages.success(request, f"Compte @{member.username} mis à jour ({member.get_role_display()}).")
    else:
        messages.error(request, "Modification invalide.")
    return redirect(back)
