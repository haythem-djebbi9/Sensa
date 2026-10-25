"""Balises de gabarit partagées : icônes, avatars, badges, ondes audio, dates."""

import hashlib
import re
from datetime import timedelta

from django import template
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

# Icônes de la maquette Figma (traits 24×24, couleur héritée).
ICONS = {
    "home": '<path d="m3 10 9-7 9 7"/><path d="M5 9v11h14V9M9 20v-6h6v6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "shield": '<path d="M12 3 4.5 6v5.5c0 4.7 3.2 8 7.5 9.5 4.3-1.5 7.5-4.8 7.5-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H3v-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6V3h4v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
    "heart": '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z"/>',
    "message": '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/>',
    "bookmark": '<path d="M6 3h12v18l-6-4-6 4Z"/>',
    "more": '<circle cx="5" cy="12" r="1" fill="currentColor"/><circle cx="12" cy="12" r="1" fill="currentColor"/><circle cx="19" cy="12" r="1" fill="currentColor"/>',
    "play": '<path d="m9 6 9 6-9 6Z" fill="currentColor"/>',
    "pause": '<path d="M9 6v12M15 6v12"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 15-5-5L5 20"/>',
    "mic": '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/>',
    "spark": '<path d="m12 2 1.4 5.6L19 9l-5.6 1.4L12 16l-1.4-5.6L5 9l5.6-1.4Z"/><path d="m19 16 .6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6Z"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "arrow": '<path d="m9 18 6-6-6-6"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "share": '<path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7"/><path d="M12 3v12M7 8l5-5 5 5"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "list": '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1" fill="currentColor"/><circle cx="4" cy="12" r="1" fill="currentColor"/><circle cx="4" cy="18" r="1" fill="currentColor"/>',
    "headphones": '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><path d="M4 14h3v6H5a1 1 0 0 1-1-1ZM20 14h-3v6h2a1 1 0 0 0 1-1Z"/>',
    "logout": '<path d="M10 4H4v16h6M14 8l4 4-4 4M8 12h10"/>',
    "filter": '<path d="M4 5h16l-6 7v6l-4 2v-8Z"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v5h16v-5"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor"/>',
    "flag": '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16Z"/><path d="m13 7 4 4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "alert": '<path d="M12 3 2 20h20Z"/><path d="M12 10v4M12 17h.01"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "lock": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "hash": '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
}


@register.simple_tag
def icon(name, size=20, label=""):
    paths = ICONS.get(name, ICONS["more"])
    aria = format_html('role="img" aria-label="{}"', label) if label else 'aria-hidden="true"'
    return mark_safe(
        f'<svg class="icon icon-{escape(name)}" {aria} width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
    )


@register.inclusion_tag("partials/logo.html")
def logo(compact=False):
    return {"compact": compact}


_AVATAR_COLORS = ["#5b3df5", "#ff6b6b", "#06b98a", "#e0a400", "#4930d9", "#d9486a", "#1e1b3a", "#2f80ed"]


@register.simple_tag
def avatar(member, size="", extra_class=""):
    """Avatar d'un membre : photo de profil ou initiales colorées."""
    css = "avatar"
    if size == "large":
        css += " avatar-large"
    elif size == "small":
        css += " avatar-small"
    if extra_class:
        css += f" {extra_class}"
    if member is None:
        return format_html('<span class="{} avatar-initials" aria-hidden="true">?</span>', css)
    profile = getattr(member, "profile", None)
    name = profile.name if profile else member.get_username()
    if profile and profile.avatar:
        return format_html('<img class="{}" src="{}" alt="Photo de profil de {}">', css, profile.avatar.url, name)
    initials = "".join(part[0] for part in re.split(r"[\s._-]+", name) if part)[:2].upper() or "?"
    digest = int(hashlib.md5(str(member.pk).encode()).hexdigest(), 16)
    color = _AVATAR_COLORS[digest % len(_AVATAR_COLORS)]
    return format_html(
        '<span class="{} avatar-initials" style="background:{}" role="img" aria-label="Avatar de {}">{}</span>',
        css,
        color,
        name,
        initials,
    )


_STATUS_BADGES = {
    "PENDING": ("analyzing", "spark", "Analyse en cours…"),
    "READY": ("ready", "check", "Prêt"),
    "UNDER_REVIEW": ("review", "eye", "En revue"),
    "FAILED": ("failed", "alert", "Analyse IA en échec"),
    "BLOCKED": ("blocked", "shield", "Bloqué"),
}


@register.simple_tag
def status_badge(publication):
    kind, icon_name, label = _STATUS_BADGES.get(publication.status, ("review", "more", publication.status))
    if publication.status == "READY" and publication.has_ai_content:
        label = "Enrichi par l'IA"
    return format_html('<span class="badge badge-{}">{}{}</span>', kind, icon(icon_name, 14), label)


@register.simple_tag
def badge(kind, text, icon_name=""):
    icons = {"ready": "check", "ai": "spark", "analyzing": "spark", "blocked": "shield", "review": "eye", "failed": "alert"}
    return format_html('<span class="badge badge-{}">{}{}</span>', kind, icon(icon_name or icons.get(kind, "more"), 14), text)


_WAVE_HEIGHTS = ["h-3", "h-4", "h-5", "h-6", "h-7", "h-8", "h-9"]


@register.simple_tag
def waveform(seed, count=20, css="waveform"):
    """Onde sonore décorative, stable pour un même audio."""
    digest = hashlib.sha256(str(seed).encode()).digest()
    bars = []
    for index in range(int(count)):
        height = _WAVE_HEIGHTS[digest[index % len(digest)] % len(_WAVE_HEIGHTS)]
        bars.append(f'<span class="wave-bar {height}"></span>')
    return mark_safe(f'<div class="{escape(css)}" aria-hidden="true">{"".join(bars)}</div>')


@register.filter
def since(value):
    """Durée courte façon réseau social : « à l'instant », « 8 min », « 3 h », « 2 j »."""
    if not value:
        return ""
    delta = timezone.now() - value
    if delta < timedelta(minutes=1):
        return "à l'instant"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} h"
    if delta < timedelta(days=7):
        return f"{delta.days} j"
    return timezone.localtime(value).strftime("%d/%m/%Y")


@register.filter
def duration(seconds):
    """Formate une durée en secondes : 102 → « 1:42 »."""
    try:
        total = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return "0:00"
    return f"{total // 60}:{total % 60:02d}"


@register.filter
def percent(value):
    try:
        return f"{round(float(value) * 100)} %"
    except (TypeError, ValueError):
        return "—"


@register.filter
def percent_int(value):
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


_HASHTAG_RE = re.compile(r"(?<![\w&])#([\w\-]{2,50})", re.UNICODE)
_MENTION_RE = re.compile(r"(?<![\w@])@([\w.\-]{2,150})", re.UNICODE)


@register.filter(needs_autoescape=True)
def rich_text(value, autoescape=True):
    """Échappe le texte puis transforme #hashtags et @mentions en liens."""
    text = escape(value or "")

    def tag_link(match):
        name = match.group(1)
        url = reverse("feed:tag", args=[name.lower()])
        return f'<a class="inline-tag" href="{url}">#{name}</a>'

    def mention_link(match):
        username = match.group(1)
        url = reverse("accounts:profile", args=[username])
        return f'<a class="inline-mention" href="{url}">@{username}</a>'

    text = _HASHTAG_RE.sub(tag_link, text)
    text = _MENTION_RE.sub(mention_link, text)
    return mark_safe(text.replace("\n", "<br>"))


@register.simple_tag(takes_context=True)
def active(context, *url_names, css="nav-active"):
    """Renvoie la classe CSS active si la vue courante fait partie des noms donnés."""
    match = getattr(context.get("request"), "resolver_match", None)
    if match and match.view_name in url_names:
        return css
    return ""


@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    """Reconstruit la query string en remplaçant certains paramètres (pagination, filtres)."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()
