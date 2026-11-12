"""Session-end trigger: run the Profile job for users whose newest unprocessed prompt is older
than 30 minutes. Meant to run every few minutes on a lab deployment."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.V2.personalization import gate, profile_job, profile_store

IDLE_MINUTES = 30


class Command(BaseCommand):
    help = "Run the Profile job for users whose last unprocessed prompt is over 30 minutes old."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="list the users, run nothing")

    def handle(self, *args, **options):
        now = timezone.now()
        idle_before = now - timedelta(minutes=IDLE_MINUTES)
        due = []
        for user_id in profile_store.users_with_new_prompts():
            if not gate.enabled_for(user_id):
                continue
            mark = profile_store.get_settings(user_id).processed_through
            prompts = profile_store.unprocessed_user_prompts(user_id, mark)
            if prompts and prompts[-1].server_timestamp <= idle_before:
                due.append(user_id)
        if options["dry_run"]:
            self.stdout.write(f"sweep dry run: {len(due)} user(s) due")
            return
        counts: dict[str, int] = {}
        for user_id in due:
            result = profile_job.run_profile_job(user_id, "idle", window="session", now=now)
            counts[result.status] = counts.get(result.status, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        self.stdout.write(f"sweep: {len(due)} user(s) run ({summary})")
