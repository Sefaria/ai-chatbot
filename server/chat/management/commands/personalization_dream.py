"""The nightly update: run the Profile job for every user with a screened prompt since their last
run, or whose Instructions changed or were toggled since then. Safe to run twice: a run moves
`last_run_at`, so the second finds nobody due."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.V2.personalization import gate, profile_job, profile_store


def due_users() -> list[str]:
    due = []
    for user_id in profile_store.users_to_screen():
        if not gate.enabled_for(user_id):
            continue
        settings_ = profile_store.get_settings(user_id)
        last_run = settings_.last_run_at
        instructions_moved = settings_.instructions_updated_at is not None and (
            last_run is None or settings_.instructions_updated_at > last_run
        )
        if instructions_moved or profile_store.unprocessed_user_prompts(user_id, last_run):
            due.append(user_id)
    return due


class Command(BaseCommand):
    help = "Run the nightly Profile update for users with new prompts or changed Instructions."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="list the users, run nothing")

    def handle(self, *args, **options):
        now = timezone.now()
        due = due_users()
        if options["dry_run"]:
            self.stdout.write(f"dream dry run: {len(due)} user(s) due")
            return
        counts: dict[str, int] = {}
        for user_id in due:
            result = profile_job.run_profile_job(user_id, "nightly", now=now)
            counts[result.status] = counts.get(result.status, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        self.stdout.write(f"dream: {len(due)} user(s) run ({summary})")
