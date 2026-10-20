"""Relance des analyses IA en masse.

    python manage.py reenrich --failed                    # relance toutes les analyses en échec
    python manage.py reenrich --all                       # réanalyse toutes les publications
    python manage.py reenrich --publication <uuid>        # réanalyse une publication
    python manage.py reenrich --all --types tags,summary  # seulement certaines étapes
"""

from django.core.management.base import BaseCommand, CommandError

from enrichment import pipeline
from enrichment.models import AnalyseIA
from publications.models import Publication


class Command(BaseCommand):
    help = "Relance des analyses IA (échecs, toutes les publications ou une seule)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--failed", action="store_true", help="Relancer les analyses en échec.")
        group.add_argument("--all", action="store_true", help="Réanalyser toutes les publications.")
        group.add_argument("--publication", help="Identifiant (UUID) d'une publication.")
        parser.add_argument("--types", default="", help="Étapes à relancer, ex. tags,summary,moderation.")

    def handle(self, *args, **options):
        types = [t.strip() for t in options["types"].split(",") if t.strip()] or None
        unknown = set(types or []) - set(AnalyseIA.Type.values)
        if unknown:
            raise CommandError(f"Étapes inconnues : {', '.join(sorted(unknown))}")
        if options["failed"]:
            publications = Publication.objects.filter(analyses__status=AnalyseIA.Status.FAILED).distinct()
            total = sum(pipeline.retry_failed(publication) for publication in publications)
            self.stdout.write(self.style.SUCCESS(f"{total} analyse(s) en échec relancée(s)."))
            return
        if options["publication"]:
            publications = Publication.objects.filter(pk=options["publication"])
            if not publications.exists():
                raise CommandError("Publication introuvable.")
        else:
            publications = Publication.objects.all()
        count = 0
        for publication in publications.iterator():
            pipeline.launch(publication, types=types)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"{count} publication(s) remise(s) dans le pipeline."))
