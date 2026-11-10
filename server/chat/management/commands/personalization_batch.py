"""Run the batch step of the configured personalization strategy (nightly, hybrid, ...)."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from chat.V2.personalization.jev import HttpJevClient
from chat.V2.personalization.llm import AnthropicLLMClient, UsageLedger
from chat.V2.personalization.registry import SlotRegistry
from chat.V2.personalization.store import DjangoProfileStore
from chat.V2.personalization.strategies.base import build_strategy


class Command(BaseCommand):
    help = "Run run_batch for settings.PERSONALIZATION_MODE (does nothing when the mode is off)."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", action="append", dest="user_ids", default=None)

    def handle(self, *args, **options):
        mode = settings.PERSONALIZATION_MODE
        if mode == "off":
            self.stdout.write("PERSONALIZATION_MODE is off; nothing to do.")
            return
        ledger = UsageLedger()
        strategy = build_strategy(
            mode,
            DjangoProfileStore(),
            SlotRegistry.load(),
            AnthropicLLMClient(),
            ledger,
            settings.PERSONALIZATION_MODEL,
            jev=HttpJevClient() if mode == "hybrid_jev" else None,
        )
        result = strategy.run_batch(timezone.now(), options["user_ids"])
        totals = ledger.totals()["all"] if ledger.entries else {}
        self.stdout.write(
            f"{mode}: {result.users_processed} users, {len(result.updated_slots)} slots updated, "
            f"{result.llm_calls} model calls, {result.errors} errors, usage {totals}"
        )
        if result.errors:
            raise CommandError(f"{result.errors} consolidation error(s); see the log")
