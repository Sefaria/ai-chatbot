"""Run periodically to physically remove expired ambient transcript state."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import ListeningSession


class Command(BaseCommand):
    help = "Clear expired listening transcripts; remove session rows after the rate-limit window."

    def handle(self, *args, **options):
        now = timezone.now()
        ListeningSession.objects.filter(expires_at__lte=now).update(
            state={}, closed=True, lease=None
        )
        count, _ = ListeningSession.objects.filter(
            created_at__lt=now - timedelta(hours=2),
        ).delete()
        self.stdout.write(f"Removed {count} listening sessions")
