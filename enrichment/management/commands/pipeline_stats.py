from django.core.management.base import BaseCommand

from enrichment.views import dashboard_stats


def _ms(value):
    if value is None:
        return "—"
    return f"{value} ms" if value < 1000 else f"{value / 1000:.1f} s"


class Command(BaseCommand):
    help = "Statistiques du pipeline IA (par étape et par fournisseur)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)

    def handle(self, *args, **options):
        stats = dashboard_stats(options["hours"])
        rate = f"{stats['success_rate']} %" if stats["success_rate"] is not None else "—"
        self.stdout.write(
            f"{stats['total']} analyses terminées · réussite {rate} · latence moyenne {_ms(stats['avg_ms'])} · "
            f"p95 {_ms(stats['p95_ms'])} · {stats['tokens']} jetons · {stats['cached']} depuis le cache"
        )
        self.stdout.write(f"{'Étape':<30}{'Total':>7}{'Réussite':>10}{'Moy.':>10}{'p95':>10}{'Relancées':>11}")
        for row in stats["per_type"]:
            self.stdout.write(
                f"{row['label']:<30}{row['total']:>7}{str(row['success_rate']) + ' %':>10}"
                f"{_ms(row['avg_ms']):>10}{_ms(row['p95_ms']):>10}{row['retried']:>11}"
            )
        for provider in stats["providers"]:
            self.stdout.write(f"  · {provider['name']} : {provider['count']} ({provider['pct']} %)")
