from django.core.management.base import BaseCommand

from enrichment.watchdog import requeue_stuck


class Command(BaseCommand):
    help = "Remet en file les analyses IA bloquées (worker arrêté, serveur redémarré…)."

    def handle(self, *args, **options):
        count = requeue_stuck()
        self.stdout.write(self.style.SUCCESS(f"{count} analyse(s) ou pipeline(s) relancé(s)."))
