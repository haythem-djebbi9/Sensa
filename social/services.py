"""Services sociaux : notifications temps réel et annotations des cartes de posts."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Count, Exists, OuterRef

from .models import Notification, Reaction

logger = logging.getLogger("sensa")


def group_name(user_id):
    return f"notifications_{user_id}"


def notification_payload(notification):
    from django.urls import reverse

    return {
        "type": "notification",
        "id": notification.pk,
        "title": notification.title,
        "message": notification.message,
        "url": reverse("social:notification_open", args=[notification.pk]),
        "unread": Notification.objects.filter(recipient_id=notification.recipient_id, is_read=False).count(),
    }


def push_notification(notification):
    """Envoie la notification au navigateur du destinataire via WebSocket."""
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            group_name(notification.recipient_id), {"type": "notify", "payload": notification_payload(notification)}
        )
    except Exception:  # le temps réel ne doit jamais casser une action
        logger.warning("Notification temps réel non envoyée", exc_info=True)


def notify(recipient, notif_type, *, actor=None, publication=None, commentaire=None, message="",
           priority=Notification.Priority.NORMAL):
    """Crée une notification (jamais à soi-même) et la pousse en temps réel après commit."""
    if actor is not None and actor.pk == recipient.pk:
        return None
    notification = Notification.objects.create(
        recipient=recipient,
        actor=actor,
        type=notif_type,
        publication=publication,
        commentaire=commentaire,
        message=message[:255],
        priority=priority,
    )
    transaction.on_commit(lambda: push_notification(notification))
    return notification


def annotate_social(queryset, user):
    """Ajoute like_count, comment_count, republish_count, liked, republished et saved (par l'utilisateur courant)."""
    from publications.models import Enregistrement, Republication

    queryset = queryset.annotate(
        like_count=Count("reactions", distinct=True),
        comment_count=Count("commentaires", distinct=True),
        republish_count=Count("republications", distinct=True),
    )
    if user.is_authenticated:
        queryset = queryset.annotate(
            liked=Exists(Reaction.objects.filter(publication=OuterRef("pk"), member=user, type=Reaction.Type.LIKE)),
            republished=Exists(Republication.objects.filter(publication=OuterRef("pk"), member=user)),
            saved=Exists(Enregistrement.objects.filter(publication=OuterRef("pk"), member=user)),
        )
    return queryset
