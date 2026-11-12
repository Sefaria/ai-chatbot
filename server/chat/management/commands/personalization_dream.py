"""The nightly dream: rebuild the Profile from the last 60 days for users active since their last
dream."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import ProfileWriterRun
from chat.V2.personalization import gate, profile_job, profile_store

WINDOW_DAYS = profile_job.STALE_DAYS
DONE_STATUSES = ("applied", "no_change")  # a failed or rejected dream is retried the next night


def _last_dream(user_id: str):
    return (
        ProfileWriterRun.objects.filter(user_id=user_id, window="dream", status__in=DONE_STATUSES)
        .order_by("-started_at")
        .values_list("started_at", flat=True)
        .first()
    )


class Command(BaseCommand):
    help = "Run the dream for users with new prompts since their last dream."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="list the users, run nothing")

    def handle(self, *args, **options):
        now = timezone.now()
        floor = now - timedelta(days=WINDOW_DAYS)
        due = []
        for user_id in profile_store.users_active_since(floor):
            if not gate.enabled_for(user_id):
                continue
            since = max(filter(None, (floor, _last_dream(user_id))))
            if profile_store.unprocessed_user_prompts(user_id, since):
                due.append(user_id)
        if options["dry_run"]:
            self.stdout.write(f"dream dry run: {len(due)} user(s) due")
            return
        counts: dict[str, int] = {}
        for user_id in due:
            result = profile_job.run_profile_job(user_id, "dream", window="dream", now=now)
            counts[result.status] = counts.get(result.status, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        self.stdout.write(f"dream: {len(due)} user(s) run ({summary})")
