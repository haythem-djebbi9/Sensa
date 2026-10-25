from django.conf import settings


def sensa(request):
    """Variables disponibles dans tous les gabarits."""
    context = {
        "SENSA_MAX_TEXT_LENGTH": settings.SENSA_MAX_TEXT_LENGTH,
        "SENSA_MAX_IMAGES": settings.SENSA_MAX_IMAGES,
        "unread_notifications": 0,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from social.models import Notification

        context["unread_notifications"] = Notification.objects.filter(recipient=user, is_read=False).count()
    return context
