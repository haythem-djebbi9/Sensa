import json
import statistics
from collections import defaultdict
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import admin_required, moderator_required
from accounts.views import safe_next
from moderation.models import ConfigurationIA

from . import pipeline
from .dispatch import enqueue
from .models import AnalyseIA, CacheIA, PipelineConfig
from .subtitles import to_srt, to_txt, to_vtt
from .watchdog import requeue_stuck, stuck_queryset

S = AnalyseIA.Status


class PipelineConfigForm(forms.ModelForm):
    class Meta:
        model = PipelineConfig
        fields = ("describe_images", "transcribe_audio", "generate_tags", "summarize", "max_tags", "whisper_model",
                  "language_hint", "use_cache", "stuck_after_minutes")
        widgets = {
            "max_tags": forms.NumberInput(attrs={"min": 3, "max": 8}),
            "stuck_after_minutes": forms.NumberInput(attrs={"min": 2, "max": 120}),
        }

    def clean_max_tags(self):
        value = self.cleaned_data["max_tags"]
        if not 3 <= value <= 8:
            raise forms.ValidationError("Entre 3 et 8 tags.")
        return value

    def clean_stuck_after_minutes(self):
        value = self.cleaned_data["stuck_after_minutes"]
        if not 2 <= value <= 120:
            raise forms.ValidationError("Entre 2 et 120 minutes.")
        return value


def _percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))]


def dashboard_stats(hours=24):
    """Indicateurs du tableau de bord sur les `hours` dernières heures."""
    since = timezone.now() - timedelta(hours=hours)
    rows = list(
        AnalyseIA.objects.filter(updated_at__gte=since).values(
            "type", "status", "duration_ms", "tokens", "provider", "cached", "finished_at", "attempts"
        )
    )
    finished = [row for row in rows if row["status"] in (S.DONE, S.FAILED)]
    done = [row for row in finished if row["status"] == S.DONE]
    durations = [row["duration_ms"] for row in done if row["duration_ms"] is not None and not row["cached"]]

    per_type = []
    for type_value, label in AnalyseIA.Type.choices:
        typed = [row for row in finished if row["type"] == type_value]
        if not typed:
            continue
        ok = [row for row in typed if row["status"] == S.DONE]
        typed_durations = [row["duration_ms"] for row in ok if row["duration_ms"] is not None and not row["cached"]]
        per_type.append({
            "type": type_value,
            "label": label,
            "total": len(typed),
            "done": len(ok),
            "failed": len(typed) - len(ok),
            "success_rate": round(len(ok) / len(typed) * 100),
            "avg_ms": int(statistics.mean(typed_durations)) if typed_durations else None,
            "p95_ms": _percentile(typed_durations, 0.95),
            "retried": sum(1 for row in typed if row["attempts"] > 1),
            "cached": sum(1 for row in ok if row["cached"]),
        })

    # Histogramme horaire : analyses réussies et en échec par heure.
    now = timezone.localtime()
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
    counts = defaultdict(lambda: {"done": 0, "failed": 0})
    for row in finished:
        if row["finished_at"]:
            hour = timezone.localtime(row["finished_at"]).replace(minute=0, second=0, microsecond=0)
            counts[hour]["done" if row["status"] == S.DONE else "failed"] += 1
    peak = max([c["done"] + c["failed"] for c in counts.values()] or [0])
    timeline = []
    for index in range(hours):
        hour = start + timedelta(hours=index)
        bucket = counts.get(hour, {"done": 0, "failed": 0})
        timeline.append({
            "label": hour.strftime("%Hh"),
            "done": bucket["done"],
            "failed": bucket["failed"],
            "total": bucket["done"] + bucket["failed"],
            "done_pct": round(bucket["done"] / peak * 100, 1) if peak else 0,
            "failed_pct": round(bucket["failed"] / peak * 100, 1) if peak else 0,
        })

    providers = defaultdict(int)
    for row in done:
        providers[row["provider"] or "—"] += 1
    total_done = sum(providers.values())
    provider_rows = sorted(
        ({"name": name, "count": count, "pct": round(count / total_done * 100) if total_done else 0}
         for name, count in providers.items()),
        key=lambda item: item["count"],
        reverse=True,
    )
    return {
        "total": len(finished),
        "done": len(done),
        "failed": len(finished) - len(done),
        "active": sum(1 for row in rows if row["status"] in (S.PENDING, S.RUNNING, S.RETRY)),
        "retry": sum(1 for row in rows if row["status"] == S.RETRY),
        "success_rate": round(len(done) / len(finished) * 100) if finished else None,
        "avg_ms": int(statistics.mean(durations)) if durations else None,
        "p95_ms": _percentile(durations, 0.95),
        "tokens": sum(row["tokens"] or 0 for row in rows),
        "cached": sum(1 for row in done if row["cached"]),
        "per_type": per_type,
        "timeline": timeline,
        "timeline_peak": peak,
        "providers": provider_rows,
    }


@moderator_required
def monitor(request):
    """Tableau de bord du pipeline IA : indicateurs, étapes, chronologie, fournisseurs et analyses."""
    analyses = AnalyseIA.objects.select_related("publication", "publication__author").order_by("-updated_at")
    status = request.GET.get("statut", "")
    analysis_type = request.GET.get("type", "")
    if status in S.values:
        analyses = analyses.filter(status=status)
    if analysis_type in AnalyseIA.Type.values:
        analyses = analyses.filter(type=analysis_type)
    config = PipelineConfig.get_solo()
    cache_stats = CacheIA.objects.aggregate(entries=Count("id"), hits=Sum("hits"))
    context = {
        "page": Paginator(analyses, 25).get_page(request.GET.get("page")),
        "status": status,
        "analysis_type": analysis_type,
        "statuses": S.choices,
        "types": AnalyseIA.Type.choices,
        "stats": dashboard_stats(),
        "cache_stats": {"entries": cache_stats["entries"] or 0, "hits": cache_stats["hits"] or 0},
        "max_retries": ConfigurationIA.get_solo().max_retries,
        "stuck_count": stuck_queryset().count(),
        "failed_count": AnalyseIA.objects.filter(status=S.FAILED).count(),
        "stuck_before": timezone.now() - timedelta(minutes=config.stuck_after_minutes),
        "config": config,
        "config_form": PipelineConfigForm(instance=config) if request.user.is_admin_role else None,
    }
    return render(request, "enrichment/monitor.html", context)


@moderator_required
def analysis_detail(request, pk):
    analysis = get_object_or_404(AnalyseIA.objects.select_related("publication", "publication__author"), pk=pk)
    return render(
        request,
        "enrichment/analysis_detail.html",
        {
            "analysis": analysis,
            "attempts": analysis.tentatives.all(),
            "result_json": json.dumps(analysis.result, ensure_ascii=False, indent=2),
            "max_retries": ConfigurationIA.get_solo().max_retries,
        },
    )


def _requeue(analysis):
    """Remet une analyse en file et relance ce qu'il faut (après commit)."""
    analysis.status = S.PENDING
    analysis.attempts = 0
    analysis.error = ""
    analysis.started_at = analysis.finished_at = analysis.duration_ms = None
    analysis.save(update_fields=["status", "attempts", "error", "started_at", "finished_at", "duration_ms", "updated_at"])
    if analysis.type == AnalyseIA.Type.TONE:
        from social.tasks import analyse_tone

        comment_id = int(analysis.target_id)
        transaction.on_commit(lambda: enqueue(analyse_tone, [comment_id]))
    elif analysis.type == AnalyseIA.Type.TRANSLATION:
        from .tasks import translate_transcription

        audio_id, language = analysis.target_id.split(":", 1)
        transaction.on_commit(lambda: enqueue(translate_transcription, [int(audio_id), language]))
    else:
        from publications.models import Publication

        Publication.objects.filter(pk=analysis.publication_id).update(status=Publication.Status.PENDING)
        publication_id = analysis.publication_id
        transaction.on_commit(lambda: pipeline.advance(publication_id))


@moderator_required
@require_POST
def retry_analysis(request, pk):
    """Remet une analyse en file (échec ou bloquée) et relance le pipeline."""
    analysis = get_object_or_404(AnalyseIA, pk=pk)
    with transaction.atomic():
        _requeue(analysis)
    messages.success(request, f"Analyse « {analysis.get_type_display()} » relancée.")
    return redirect(safe_next(request, reverse("enrichment:monitor")))


@moderator_required
@require_POST
def retry_all_failed(request):
    failed = list(AnalyseIA.objects.filter(status=S.FAILED))
    with transaction.atomic():
        for analysis in failed:
            _requeue(analysis)
    messages.success(request, f"{len(failed)} analyse(s) en échec relancée(s).")
    return redirect(safe_next(request, reverse("enrichment:monitor")))


@moderator_required
@require_POST
def run_watchdog(request):
    count = requeue_stuck()
    if count:
        messages.success(request, f"Chien de garde : {count} analyse(s) bloquée(s) remise(s) en file.")
    else:
        messages.info(request, "Aucune analyse bloquée.")
    return redirect(safe_next(request, reverse("enrichment:monitor")))


@admin_required
@require_POST
def update_config(request):
    config = PipelineConfig.get_solo()
    form = PipelineConfigForm(request.POST, instance=config)
    if form.is_valid():
        config = form.save(commit=False)
        config.updated_by = request.user
        config.save()
        messages.success(request, "Configuration du pipeline enregistrée.")
    else:
        errors = "; ".join(error for field_errors in form.errors.values() for error in field_errors)
        messages.error(request, f"Configuration invalide : {errors}")
    return redirect(f"{reverse('enrichment:monitor')}#configuration")


@login_required
@require_POST
def reenrich(request, pk):
    """Réanalyse complète d'une publication (auteur ou modérateur)."""
    from publications.models import Publication

    publication = get_object_or_404(Publication, pk=pk)
    if not (publication.can_edit(request.user) or request.user.is_moderator):
        raise PermissionDenied
    pipeline.launch(publication)
    messages.success(request, "Nouvelle analyse IA lancée : la publication sera enrichie dans quelques secondes.")
    return redirect(safe_next(request, publication.get_absolute_url()))


def _visible_audio(request, pk):
    from publications.models import Audio, Publication

    audio = get_object_or_404(Audio.objects.select_related("publication"), pk=pk)
    if not Publication.objects.visible_to(request.user).filter(pk=audio.publication_id).exists():
        raise Http404
    return audio


SUBTITLE_FORMATS = {
    "vtt": (to_vtt, "text/vtt"),
    "srt": (to_srt, "application/x-subrip"),
    "txt": (to_txt, "text/plain"),
}


@login_required
def subtitles(request, pk, fmt):
    """Export de la transcription en sous-titres WebVTT / SRT ou en texte brut."""
    if fmt not in SUBTITLE_FORMATS:
        raise Http404
    audio = _visible_audio(request, pk)
    if not audio.transcription:
        raise Http404("Pas de transcription.")
    renderer, content_type = SUBTITLE_FORMATS[fmt]
    response = HttpResponse(renderer(audio), content_type=f"{content_type}; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="sensa-{str(audio.publication_id)[:8]}.{fmt}"'
    return response


@login_required
@require_POST
def translate(request, pk):
    from .tasks import TRANSLATION_LANGUAGES, translate_transcription

    audio = _visible_audio(request, pk)
    language = request.POST.get("lang", "")
    back = f"{audio.publication.get_absolute_url()}#traductions"
    if language not in TRANSLATION_LANGUAGES or not audio.transcription:
        messages.error(request, "Traduction impossible.")
        return redirect(back)
    with transaction.atomic():
        analysis, created = AnalyseIA.objects.get_or_create(
            publication=audio.publication, type=AnalyseIA.Type.TRANSLATION, target_id=f"{audio.pk}:{language}"
        )
        if not created and analysis.status == S.DONE:
            messages.info(request, "Cette traduction est déjà disponible.")
            return redirect(back)
        if not created and analysis.status != S.PENDING:
            analysis.status, analysis.attempts, analysis.error = S.PENDING, 0, ""
            analysis.save(update_fields=["status", "attempts", "error", "updated_at"])
        audio_id = audio.pk
        transaction.on_commit(lambda: enqueue(translate_transcription, [audio_id, language]))
    messages.success(request, "Traduction demandée : elle apparaît dans quelques secondes.")
    return redirect(back)
