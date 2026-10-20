"""Logique de relance commune à toutes les tâches IA (module 3).

Chaque tâche Celery des modules 1 à 5 passe par `run_step` :
  1. la ligne AnalyseIA est « réservée » (PENDING/RETRY → RUNNING, tentatives + 1) ;
  2. le traitement est exécuté ; les appels IA (fournisseur, modèle, jetons, latence)
     sont collectés et la tentative est journalisée dans TentativeIA ;
  3. en cas d'erreur, relance après 10 s, 1 min puis 5 min ; au-delà de
     `ConfigurationIA.max_retries` tentatives, l'analyse passe en FAILED ;
  4. une fois terminée (DONE ou FAILED), l'auteur reçoit la progression en temps réel
     et le pipeline avance à l'étape suivante.
"""

import logging
import time
from dataclasses import dataclass, field

from django.conf import settings
from django.db.models import DateTimeField, F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.ai import usage

from .dispatch import enqueue
from .models import AnalyseIA, TentativeIA

logger = logging.getLogger("sensa.ai")


@dataclass
class StepResult:
    result: dict = field(default_factory=dict)
    score: float | None = None
    provider: str = ""
    model: str = ""
    cached: bool = False


def max_retries():
    from moderation.models import ConfigurationIA

    return max(1, ConfigurationIA.get_solo().max_retries)


def backoff_for(attempt):
    delays = settings.SENSA_RETRY_BACKOFF
    return delays[min(max(attempt, 1) - 1, len(delays) - 1)]


def ensure_analysis(publication, analysis_type, target_id):
    """Ligne AnalyseIA de cette cible (créée en file si la tâche est appelée directement)."""
    analysis, _ = AnalyseIA.objects.get_or_create(
        publication=publication, type=analysis_type, target_id=str(target_id)
    )
    return analysis


def _summarize_calls(calls):
    """Fournisseur et modèle du dernier appel, total des jetons."""
    if not calls:
        return "", "", 0
    last = calls[-1]
    return last["provider"], last["model"], sum(call["tokens"] for call in calls)


def run_step(task, analysis, handler, *, task_args):
    """Exécute `handler(analysis, last_attempt)` avec la politique de relance commune."""
    now = timezone.now()
    claimed = AnalyseIA.objects.filter(
        pk=analysis.pk, status__in=[AnalyseIA.Status.PENDING, AnalyseIA.Status.RETRY]
    ).update(status=AnalyseIA.Status.RUNNING, attempts=F("attempts") + 1, started_at=Coalesce(F("started_at"), Value(now, output_field=DateTimeField())))
    if not claimed:
        # Déjà traitée ou en cours ailleurs : rien à faire (tâche idempotente).
        return None
    analysis.refresh_from_db()
    limit = max_retries()
    last_attempt = analysis.attempts >= limit
    attempt_started = timezone.now()
    clock = time.monotonic()

    outcome, failure = None, None
    with usage.collect() as calls:
        try:
            outcome = handler(analysis, last_attempt) or StepResult()
        except Exception as exc:  # toute erreur compte comme un échec de tentative
            failure = f"{type(exc).__name__}: {exc}"[:1000]

    provider, model, tokens = _summarize_calls(calls)
    if outcome is not None:
        provider = outcome.provider or provider
        model = outcome.model or model
    TentativeIA.objects.create(
        analyse=analysis,
        number=analysis.attempts,
        provider=provider[:40],
        model=model[:80],
        success=failure is None,
        error=failure or "",
        tokens=tokens,
        started_at=attempt_started,
        duration_ms=int((time.monotonic() - clock) * 1000),
    )
    analysis.tokens += tokens

    if failure is not None:
        analysis.error = failure
        logger.warning(
            "Analyse %s #%s : tentative %s/%s échouée (%s)",
            analysis.type, analysis.pk, analysis.attempts, limit, failure,
        )
        if not last_attempt:
            analysis.status = AnalyseIA.Status.RETRY
            analysis.save(update_fields=["status", "error", "tokens", "updated_at"])
            enqueue(task, task_args, countdown=backoff_for(analysis.attempts))
            _push(analysis)
            return analysis
        analysis.status = AnalyseIA.Status.FAILED
    else:
        analysis.result = outcome.result
        analysis.score = outcome.score
        analysis.provider = provider[:40]
        analysis.model = model[:80]
        analysis.cached = outcome.cached
        analysis.error = ""
        analysis.status = AnalyseIA.Status.DONE

    analysis.finished_at = timezone.now()
    if analysis.started_at:
        analysis.duration_ms = int((analysis.finished_at - analysis.started_at).total_seconds() * 1000)
    analysis.save()

    if analysis.type not in AnalyseIA.SIDE_TYPES:
        from .pipeline import advance

        _push(analysis)
        advance(analysis.publication_id)
    return analysis


def _push(analysis):
    if analysis.type in AnalyseIA.SIDE_TYPES:
        return
    from .realtime import push_pipeline

    push_pipeline(analysis.publication_id)
