"""Bring findings in line with their sheets: ``python manage.py sync_findings``.

Run nightly. A sheet edited since its finding was saved has its refs re-read; a deleted sheet,
or one that changed owner, hides its finding.
"""

from collections import Counter

from django.core.management.base import BaseCommand

from findings import service
from findings.models import Finding


class Command(BaseCommand):
    help = "Re-read the sheets behind findings and update or hide them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0, help="Sync at most this many (oldest synced first)"
        )

    def handle(self, *args, limit=0, **options):
        qs = Finding.objects.filter(hidden=False).order_by("synced")
        if limit:
            qs = qs[:limit]
        results = Counter(service.sync(f) for f in qs.iterator())
        summary = ", ".join(f"{n} {what}" for what, n in sorted(results.items())) or "nothing to do"
        self.stdout.write(f"Synced findings: {summary}")
