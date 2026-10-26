"""Écrans de gestion du module 3 : tags, vocabulaire, prompts, laboratoire, digests, corrections."""

import json
from datetime import datetime, time
from difflib import SequenceMatcher

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import admin_required, moderator_required

from . import lab, prompts, transcripts, vocabulary
from .dispatch import enqueue
from .forms import DigestForm, LabForm, PromptForm, TagForm, TagMergeForm, VocabulaireForm
from .models import DigestIA, PromptIA, PublicationTag, Tag, VocabulaireIA


def confirm_delete(request, *, title, description, cancel_url, consequences=""):
    return render(
        request,
        "enrichment/confirm_delete.html",
        {"title": title, "description": description, "cancel_url": cancel_url, "consequences": consequences},
    )


# --- Tags -------------------------------------------------------------------------

def _annotated_tags():
    return Tag.objects.annotate(
        uses=Count("links", distinct=True),
        ai_uses=Count("links", filter=Q(links__source=PublicationTag.Source.AI), distinct=True),
        author_uses=Count("links", filter=Q(links__source=PublicationTag.Source.AUTHOR), distinct=True),
        avg_confidence=Avg("links__confidence"),
        last_used=Max("links__created_at"),
    )


def probable_duplicates(limit=8):
    """Paires de tags qui se ressemblent (variante orthographique ou collage de deux tags)."""
    tags = list(_annotated_tags().filter(uses__gt=0).order_by("-uses")[:400])
    names = {tag.name: tag for tag in tags}
    pairs = []
    for i, first in enumerate(tags):
        for second in tags[i + 1:]:
            if SequenceMatcher(None, first.name, second.name).ratio() >= 0.85:
                keep, drop = (first, second) if first.uses >= second.uses else (second, first)
                pairs.append({"keep": keep, "drop": drop, "reason": "Orthographe proche"})
        for a in names:
            if a != first.name and first.name.startswith(a) and first.name[len(a):] in names and first.name[len(a):] != a:
                pairs.append({"keep": names[a], "drop": first, "reason": f"Collage de #{a} et #{first.name[len(a):]}"})
                break
        if len(pairs) >= limit:
            break
    return pairs[:limit]


def merge_tags(source, target):
    """Déplace les liens de `source` vers `target` puis supprime `source`. Renvoie le nombre de liens déplacés."""
    moved = 0
    with transaction.atomic():
        for link in source.links.select_related("publication"):
            if PublicationTag.objects.filter(publication=link.publication, tag=target).exists():
                link.delete()
            else:
                link.tag = target
                link.save(update_fields=["tag"])
                moved += 1
        source.delete()
    return moved


@moderator_required
def tag_list(request):
    tags = _annotated_tags()
    query = request.GET.get("q", "").strip().lstrip("#")
    status = request.GET.get("statut", "")
    sort = request.GET.get("tri", "usage")
    if query:
        tags = tags.filter(name__icontains=query)
    if status in Tag.Status.values:
        tags = tags.filter(status=status)
    ordering = {"nom": ["name"], "recent": ["-created_at"], "usage": ["-uses", "name"]}.get(sort, ["-uses", "name"])
    page = Paginator(tags.order_by(*ordering), 30).get_page(request.GET.get("page"))
    totals = Tag.objects.aggregate(
        total=Count("id"),
        blocked=Count("id", filter=Q(status=Tag.Status.BLOCKED)),
    )
    totals["unused"] = Tag.objects.filter(links__isnull=True).count()
    totals["ai_links"] = PublicationTag.objects.filter(source=PublicationTag.Source.AI).count()
    return render(request, "enrichment/tags_list.html", {
        "page": page, "query": query, "status": status, "sort": sort, "statuses": Tag.Status.choices,
        "totals": totals, "duplicates": probable_duplicates(),
    })


@moderator_required
def tag_create(request):
    form = TagForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tag = form.save()
        messages.success(request, f"Tag #{tag.name} créé.")
        return redirect("enrichment:tag_detail", pk=tag.pk)
    return render(request, "enrichment/tag_form.html", {"form": form, "title": "Nouveau tag"})


@moderator_required
def tag_detail(request, pk):
    from publications.models import Publication

    tag = get_object_or_404(_annotated_tags(), pk=pk)
    links = (
        PublicationTag.objects.filter(tag=tag, publication__in=Publication.objects.visible_to(request.user))
        .select_related("publication", "publication__author")
        .order_by("-created_at")[:50]
    )
    return render(request, "enrichment/tag_detail.html", {"tag": tag, "links": links, "merge_form": TagMergeForm(source=tag)})


@moderator_required
def tag_update(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    form = TagForm(request.POST or None, instance=tag)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Tag #{tag.name} mis à jour.")
        return redirect("enrichment:tag_detail", pk=tag.pk)
    return render(request, "enrichment/tag_form.html", {"form": form, "title": f"Modifier #{tag.name}", "tag": tag})


@moderator_required
@require_POST
def tag_merge(request, pk):
    source = get_object_or_404(Tag, pk=pk)
    form = TagMergeForm(request.POST, source=source)
    if not form.is_valid():
        messages.error(request, "Choisissez le tag à conserver.")
        return redirect("enrichment:tag_detail", pk=source.pk)
    target = form.cleaned_data["target"]
    name = source.name
    moved = merge_tags(source, target)
    messages.success(request, f"#{name} fusionné dans #{target.name} ({moved} publication(s) déplacée(s)).")
    return redirect("enrichment:tag_detail", pk=target.pk)


@moderator_required
def tag_delete(request, pk):
    tag = get_object_or_404(_annotated_tags(), pk=pk)
    if request.method == "POST":
        name = tag.name
        tag.delete()
        messages.success(request, f"Tag #{name} supprimé.")
        return redirect("enrichment:tags")
    return confirm_delete(
        request, title=f"Supprimer #{tag.name} ?", description=f"Ce tag est utilisé par {tag.uses} publication(s).",
        consequences="Il sera retiré de toutes les publications. Pour éviter que l'IA le propose à nouveau, passez-le plutôt en « Interdit ».",
        cancel_url=reverse("enrichment:tag_detail", args=[tag.pk]),
    )


# --- Vocabulaire de transcription ------------------------------------------------

@moderator_required
def vocabulary_list(request):
    terms = VocabulaireIA.objects.all()
    query = request.GET.get("q", "").strip()
    category = request.GET.get("categorie", "")
    if query:
        terms = terms.filter(Q(term__icontains=query) | Q(variants__icontains=query))
    if category in VocabulaireIA.Category.values:
        terms = terms.filter(category=category)
    return render(request, "enrichment/vocabulary_list.html", {
        "page": Paginator(terms.order_by("term"), 40).get_page(request.GET.get("page")),
        "query": query, "category": category, "categories": VocabulaireIA.Category.choices,
        "whisper_prompt": vocabulary.whisper_prompt(),
        "active_count": VocabulaireIA.objects.filter(is_active=True).count(),
    })


@moderator_required
def vocabulary_create(request):
    form = VocabulaireForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.created_by = request.user
        entry.save()
        messages.success(request, f"« {entry.term} » ajouté au vocabulaire de transcription.")
        return redirect("enrichment:vocabulary")
    return render(request, "enrichment/vocabulary_form.html", {"form": form, "title": "Nouveau terme"})


@moderator_required
def vocabulary_update(request, pk):
    entry = get_object_or_404(VocabulaireIA, pk=pk)
    form = VocabulaireForm(request.POST or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"« {entry.term} » mis à jour.")
        return redirect("enrichment:vocabulary")
    return render(request, "enrichment/vocabulary_form.html", {"form": form, "title": f"Modifier « {entry.term} »", "entry": entry})


@moderator_required
@require_POST
def vocabulary_toggle(request, pk):
    entry = get_object_or_404(VocabulaireIA, pk=pk)
    entry.is_active = not entry.is_active
    entry.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"« {entry.term} » {'activé' if entry.is_active else 'désactivé'}.")
    return redirect("enrichment:vocabulary")


@moderator_required
def vocabulary_delete(request, pk):
    entry = get_object_or_404(VocabulaireIA, pk=pk)
    if request.method == "POST":
        term = entry.term
        entry.delete()
        messages.success(request, f"« {term} » supprimé du vocabulaire.")
        return redirect("enrichment:vocabulary")
    return confirm_delete(
        request, title=f"Supprimer « {entry.term} » ?", description="Ce terme ne sera plus transmis à Whisper.",
        consequences="Les transcriptions déjà réalisées ne sont pas modifiées.", cancel_url=reverse("enrichment:vocabulary"),
    )


# --- Prompts IA versionnés -------------------------------------------------------

@admin_required
def prompt_list(request):
    tasks = []
    for value, label in PromptIA.Task.choices:
        versions = list(PromptIA.objects.filter(task=value).select_related("created_by"))
        active = next((prompt for prompt in versions if prompt.is_active), None)
        tasks.append({
            "value": value, "label": label, "versions": versions, "active": active,
            "default": prompts.default_template(value),
            "variables": sorted(prompts.VARIABLES[value]["allowed"]),
            "required": sorted(prompts.VARIABLES[value]["required"]),
        })
    return render(request, "enrichment/prompts_list.html", {"tasks": tasks})


@admin_required
def prompt_create(request):
    task = request.GET.get("tache", PromptIA.Task.TAGS)
    if task not in PromptIA.Task.values:
        task = PromptIA.Task.TAGS
    template, _ = prompts.resolve(task)
    form = PromptForm(request.POST or None, initial={"task": task, "template": template})
    if request.method == "POST" and form.is_valid():
        prompt = form.save(commit=False)
        prompt.created_by = request.user
        prompt.save()
        if form.cleaned_data.get("activate_now"):
            prompts.activate(prompt)
        messages.success(request, f"{prompt} enregistré{' et activé' if prompt.is_active else ''}.")
        return redirect("enrichment:prompts")
    return render(request, "enrichment/prompt_form.html", {"form": form, "title": "Nouvelle version de prompt",
                                                            "variables": prompts.VARIABLES})


@admin_required
def prompt_update(request, pk):
    prompt = get_object_or_404(PromptIA, pk=pk)
    form = PromptForm(request.POST or None, instance=prompt, initial={"activate_now": prompt.is_active})
    if request.method == "POST" and form.is_valid():
        prompt = form.save()
        if form.cleaned_data.get("activate_now"):
            prompts.activate(prompt)
        messages.success(request, f"{prompt} mis à jour.")
        return redirect("enrichment:prompts")
    return render(request, "enrichment/prompt_form.html", {"form": form, "title": f"Modifier {prompt}", "prompt": prompt,
                                                            "variables": prompts.VARIABLES})


@admin_required
@require_POST
def prompt_activate(request, pk):
    prompt = get_object_or_404(PromptIA, pk=pk)
    prompts.activate(prompt)
    messages.success(request, f"{prompt} est maintenant la version active.")
    return redirect("enrichment:prompts")


@admin_required
@require_POST
def prompt_use_default(request, task):
    if task not in PromptIA.Task.values:
        raise PermissionDenied
    PromptIA.objects.filter(task=task, is_active=True).update(is_active=False)
    messages.success(request, f"« {PromptIA.Task(task).label} » utilise de nouveau le prompt par défaut.")
    return redirect("enrichment:prompts")


@admin_required
def prompt_delete(request, pk):
    prompt = get_object_or_404(PromptIA, pk=pk)
    if request.method == "POST":
        label = str(prompt)
        prompt.delete()
        messages.success(request, f"{label} supprimé.")
        return redirect("enrichment:prompts")
    return confirm_delete(
        request, title=f"Supprimer {prompt} ?", description=prompt.notes or prompt.name,
        consequences="Si c'est la version active, la tâche reviendra au prompt par défaut.",
        cancel_url=reverse("enrichment:prompts"),
    )


# --- Laboratoire IA ----------------------------------------------------------------

@moderator_required
def laboratory(request):
    initial = {"task": request.GET.get("tache") or "tags", "prompt": request.GET.get("prompt") or None}
    form = LabForm(request.POST or None, request.FILES or None, initial=initial)
    output = None
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        output = lab.run(
            data["task"], text=data.get("text", ""), image=data.get("image"), audio=data.get("audio"),
            language=data.get("language", "en"), prompt=data.get("prompt"),
        )
        output["result_json"] = json.dumps(output["result"], ensure_ascii=False, indent=2) if output["result"] is not None else ""
        output["task_label"] = dict(lab.TASKS)[data["task"]]
    return render(request, "enrichment/laboratory.html", {"form": form, "output": output})


# --- Digests IA ----------------------------------------------------------------------

@moderator_required
def digest_list(request):
    return render(request, "enrichment/digests_list.html", {
        "page": Paginator(DigestIA.objects.select_related("created_by"), 12).get_page(request.GET.get("page")),
        "form": DigestForm(),
    })


@moderator_required
@require_POST
def digest_create(request):
    from .tasks import generate_digest

    form = DigestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Période invalide : " + " ".join(form.non_field_errors()) if form.non_field_errors() else "Période invalide.")
        return redirect("enrichment:digests")
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(form.cleaned_data["period_start"], time.min), tz)
    end = timezone.make_aware(datetime.combine(form.cleaned_data["period_end"], time.max), tz)
    digest = DigestIA.objects.create(period_start=start, period_end=end, created_by=request.user)
    digest_id = digest.pk
    transaction.on_commit(lambda: enqueue(generate_digest, [digest_id]))
    messages.success(request, "Digest en préparation : l'IA rédige la synthèse en arrière-plan.")
    return redirect("enrichment:digest_detail", pk=digest.pk)


@moderator_required
def digest_detail(request, pk):
    digest = get_object_or_404(DigestIA.objects.select_related("created_by"), pk=pk)
    return render(request, "enrichment/digest_detail.html", {"digest": digest})


@moderator_required
@require_POST
def digest_regenerate(request, pk):
    from .tasks import generate_digest

    digest = get_object_or_404(DigestIA, pk=pk)
    digest.status = DigestIA.Status.PENDING
    digest.save(update_fields=["status"])
    digest_id = digest.pk
    transaction.on_commit(lambda: enqueue(generate_digest, [digest_id]))
    messages.success(request, "Nouvelle rédaction du digest lancée.")
    return redirect("enrichment:digest_detail", pk=digest.pk)


@moderator_required
def digest_delete(request, pk):
    digest = get_object_or_404(DigestIA, pk=pk)
    if request.method == "POST":
        digest.delete()
        messages.success(request, "Digest supprimé.")
        return redirect("enrichment:digests")
    return confirm_delete(request, title=f"Supprimer « {digest} » ?", description="Cette synthèse sera définitivement supprimée.",
                          cancel_url=reverse("enrichment:digest_detail", args=[digest.pk]))


# --- Correction humaine des transcriptions -----------------------------------------

def _editable_audio(request, pk):
    from publications.models import Audio

    audio = get_object_or_404(Audio.objects.select_related("publication", "publication__author"), pk=pk)
    if not (audio.publication.can_edit(request.user) or request.user.is_moderator):
        raise PermissionDenied("Seuls l'auteur et les modérateurs peuvent corriger la transcription.")
    return audio


@login_required
def transcription_edit(request, pk):
    audio = _editable_audio(request, pk)
    errors = []
    if request.method == "POST":
        texts = request.POST.getlist("segment")
        expected = len(audio.segments) if audio.segments else 1
        if len(texts) != expected:
            errors.append("Le formulaire ne correspond plus à la transcription : rechargez la page.")
        elif any(len(t) > 1000 for t in texts):
            errors.append("Chaque passage est limité à 1 000 caractères.")
        else:
            text, segments = transcripts.edit_segments(audio, texts)
            if not text:
                errors.append("La transcription ne peut pas être vide.")
            elif text == audio.transcription:
                messages.info(request, "Aucune modification.")
                return redirect(f"{audio.publication.get_absolute_url()}#transcription")
            else:
                transcripts.save_correction(audio, request.user, text=text, segments=segments,
                                            note=request.POST.get("note", ""))
                messages.success(request, "Transcription corrigée. Tags, résumé, modération et recommandations sont recalculés.")
                return redirect(f"{audio.publication.get_absolute_url()}#transcription")
    original_text, _ = transcripts.ai_original(audio)
    rows = audio.segments or [{"start": 0, "text": audio.transcription}]
    return render(request, "enrichment/transcription_edit.html", {
        "audio": audio, "post": audio.publication, "rows": rows, "errors": errors,
        "history": audio.corrections.select_related("author")[:20],
        "can_revert": transcripts.is_corrected(audio) and original_text != audio.transcription,
    })


@login_required
@require_POST
def transcription_revert(request, pk):
    audio = _editable_audio(request, pk)
    text, segments = transcripts.ai_original(audio)
    if text == audio.transcription:
        messages.info(request, "La transcription est déjà la version produite par l'IA.")
    else:
        transcripts.save_correction(audio, request.user, text=text, segments=segments,
                                    note="Retour à la version IA", is_revert=True)
        messages.success(request, "Version IA restaurée.")
    return redirect(f"{audio.publication.get_absolute_url()}#transcription")
