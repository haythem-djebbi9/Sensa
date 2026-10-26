"""Crée la version allégée des images publiées avant son introduction."""

from django.core.management.base import BaseCommand

from publications.models import Image


class Command(BaseCommand):
    help = "Crée la version allégée (WebP, 1 280 px) des images qui n'en ont pas encore."

    def handle(self, *args, **options):
        count = 0
        for image in Image.objects.filter(display=""):
            image.save()
            count += bool(image.display)
        self.stdout.write(self.style.SUCCESS(f"{count} version(s) allégée(s) créée(s)."))
