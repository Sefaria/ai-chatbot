"""Take a finding down, or put it back: ``python manage.py hide_finding <id> --reason "..."``."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from findings.models import Finding


class Command(BaseCommand):
    help = "Hide a finding from the public (staff takedown), or show it again with --unhide."

    def add_arguments(self, parser):
        parser.add_argument("finding_id")
        parser.add_argument("--reason", default="", help="Why it was hidden; required to hide")
        parser.add_argument("--unhide", action="store_true")

    def handle(self, *args, finding_id, reason, unhide, **options):
        try:
            finding = Finding.objects.get(pk=finding_id)
        except (Finding.DoesNotExist, ValueError, ValidationError) as exc:
            raise CommandError(f"No finding {finding_id}") from exc
        if unhide:
            finding.hidden, finding.hidden_reason = False, ""
        else:
            if not reason.strip():
                raise CommandError("Give a --reason for hiding it")
            finding.hidden, finding.hidden_reason = True, reason.strip()[:200]
        finding.save(update_fields=["hidden", "hidden_reason", "updated"])
        state = "shown" if unhide else "hidden"
        self.stdout.write(f"Finding {finding.pk} ({finding.title}) is now {state}.")
