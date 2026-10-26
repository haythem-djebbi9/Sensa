from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Audio, Image


@receiver(post_delete, sender=Image)
@receiver(post_delete, sender=Audio)
def delete_media_file(sender, instance, **kwargs):
    """Supprime le fichier du stockage (disque ou MinIO) avec son enregistrement.

    La suppression attend la validation de la transaction : si elle est annulée,
    l'enregistrement revient et son fichier doit toujours exister.
    """
    for field_file in (instance.file, getattr(instance, "display", None)):
        if field_file:
            storage, name = field_file.storage, field_file.name
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))
