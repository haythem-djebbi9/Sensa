from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Membre, Profile


@receiver(post_save, sender=Membre)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(membre=instance)
