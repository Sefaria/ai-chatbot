"""Run the batch step of the configured personalization strategy (nightly, hybrid, ...)."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from chat.V2.personalization.jev import HttpJevClient
from chat.V2.personalization.llm import AnthropicLLMClient, UsageLedger
from chat.V2.personalization.registry import SlotRegistry
from chat.V2.personalization.store import DjangoProfileStore
from chat.V2.personalization.strategies.base import build_strategy

RETENTION_DAYS = 30


class Command(BaseCommand):
    help = "Run run_batch for settings.PERSONALIZATION_MODE (does nothing when the mode is off)."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", action="append", dest="user_ids", default=None)

    def handle(self, *args, **options):
        mode = settings.PERSONALIZATION_MODE
        if mode == "off":
            self.stdout.write("PERSONALIZATION_MODE is off; nothing to do.")
            return
        store = DjangoProfileStore()
        now = timezone.now()
        purged = store.purge_observations(now - timedelta(days=RETENTION_DAYS))
        self.stdout.write(f"purged {purged} observations older than {RETENTION_DAYS} days")
        ledger = UsageLedger()
        jev = None
        if mode == "hybrid_jev":
            if settings.PERSONALIZATION_ALLOW_JEV == "true":
                jev = HttpJevClient()
            else:
                self.stdout.write("PERSONALIZATION_ALLOW_JEV is not 'true'; running hybrid.")
                mode = "hybrid"
        strategy = build_strategy(
            mode,
            store,
            SlotRegistry.load(),
            AnthropicLLMClient(),
            ledger,
            settings.PERSONALIZATION_MODEL,
            jev=jev,
        )
        result = strategy.run_batch(now, options["user_ids"])
        totals = ledger.totals()["all"] if ledger.entries else {}
        self.stdout.write(
            f"{mode}: {result.users_processed} users, {len(result.updated_slots)} slots updated, "
            f"{result.writes_applied} writes applied, refused {result.writes_refused}, "
            f"{result.llm_calls} model calls, {result.errors} errors, usage {totals}"
        )
        if result.errors:
            raise CommandError(f"{result.errors} consolidation error(s); see the log")
