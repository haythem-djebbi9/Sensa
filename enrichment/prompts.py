"""Prompts IA versionnés du module 3 (tags, résumé, traduction).

Chaque tâche utilise la version active définie par un administrateur, sinon le prompt
par défaut du code. Les variables autorisées sont vérifiées à l'enregistrement.
"""

import string

from django.core.exceptions import ValidationError

from .models import PromptIA

T = PromptIA.Task

VARIABLES = {
    T.TAGS: {"required": {"text"}, "allowed": {"text", "description", "transcription", "max_tags", "categories"}},
    T.SUMMARY: {"required": {"text"}, "allowed": {"text", "description", "transcription"}},
    T.TRANSLATION: {"required": {"text", "language"}, "allowed": {"text", "language"}},
}


def default_template(task):
    from . import ai

    return {T.TAGS: ai.TAGS_PROMPT, T.SUMMARY: ai.SUMMARY_PROMPT, T.TRANSLATION: ai.TRANSLATION_PROMPT}[task]


def variables_in(template):
    try:
        return {field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(template) if field}
    except ValueError as exc:
        raise ValidationError(f"Accolades mal formées : {exc}. Doublez-les ({{{{ }}}}) pour écrire une accolade.") from exc


def validate(task, template):
    rules = VARIABLES[task]
    used = variables_in(template)
    unknown = used - rules["allowed"]
    missing = rules["required"] - used
    errors = []
    if unknown:
        errors.append("Variables inconnues : " + ", ".join(f"{{{name}}}" for name in sorted(unknown)) + ".")
    if missing:
        errors.append("Variables obligatoires manquantes : " + ", ".join(f"{{{name}}}" for name in sorted(missing)) + ".")
    if errors:
        raise ValidationError(" ".join(errors))


def resolve(task, prompt=None):
    """(modèle de prompt, libellé de version) : version imposée, sinon version active, sinon défaut."""
    if prompt is None:
        prompt = PromptIA.objects.filter(task=task, is_active=True).first()
    if prompt is None:
        return default_template(task), "défaut"
    return prompt.template, f"v{prompt.version}"


def render(task, prompt=None, **values):
    template, label = resolve(task, prompt)
    return template.format(**values), label


def activate(prompt):
    PromptIA.objects.filter(task=prompt.task, is_active=True).exclude(pk=prompt.pk).update(is_active=False)
    if not prompt.is_active:
        prompt.is_active = True
        prompt.save(update_fields=["is_active", "updated_at"])
