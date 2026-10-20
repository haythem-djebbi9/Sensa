from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Audio, Image


@receiver(post_delete, sender=Image)
@receiver(post_delete, sender=Audio)
def delete_media_file(sender, instance, **kwargs):
    """Supprime le fichier du stockage (disque ou MinIO) avec son enregistrement."""
    if instance.file:
        instance.file.delete(save=False)
