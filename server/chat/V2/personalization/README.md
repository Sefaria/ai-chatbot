# Learner-profile personalization (dark prototype)

A per-user profile of memory slots (`slots.json`, slot set `v1`) rendered into the system prompt as
a `<learner_profile>` block, plus five interchangeable strategies that keep the profile current.
Nothing here is wired into the live request path. `PERSONALIZATION_MODE` (default `off`) is read
only by `PersonalizationService` and the `personalization_batch` command; with it off the prompt is
byte-identical to the one without this package.

| mode | strategy | hot path | batch |
|---|---|---|---|
| `realtime` | `RealtimeStrategy` | one extraction call per turn | expire only |
| `nightly` | `NightlyStrategy` | log the prompt, no model call | one consolidation call per user |
| `hybrid` | `HybridStrategy` | language in code; standing instruction becomes a proposal | consolidation, minus slots with a pending proposal |
| `hybrid_jev` | `HybridJevStrategy` | language in code; Jev judges the closed-set slots and standing instructions (needs a Jev client) | consolidation of the free-text slots only |
| `explicit` | `ExplicitStrategy` | nothing | nothing; `apply_interview` writes stated slots |

Shared rules, enforced in code: sensitive turns are never stored or sent to a model; `stated`
slots are never overwritten; model output goes through `SlotRegistry.validate`; evidence quotes
must be verbatim; prompts are cut to 1,000 characters; `store_enabled` and `personalize_enabled`
are separate toggles.

## Wiring it in (not done yet)

Build one `PersonalizationService(DjangoProfileStore(), SlotRegistry.load(), strategy)` at startup.
The strategy depends on the mode: `off` builds none (`strategy=None`; `build_strategy` raises for
it), `hybrid_jev` also needs a Jev client, and every other mode takes the same arguments:

```python
mode = settings.PERSONALIZATION_MODE
strategy = (
    None
    if mode == "off"
    else build_strategy(
        mode,
        store,
        registry,
        AnthropicLLMClient(),
        ledger,
        settings.PERSONALIZATION_MODEL,
        jev=HttpJevClient() if mode == "hybrid_jev" else None,
    )
)
```

1. **Before the agent runs**, where `chat_stream_v2` builds the `MessageContext`: call
   `service.profile_context(user_id, now)` and set `profile_text` and `profile_slots_used` on it.
   `build_turn_prompt` and `BraintrustTraceLogger.log_input` already read them.
2. **After the turn is saved**, right after `summary_service.update_summary(...)`: call
   `service.after_turn(Turn(user_id, session_id, message_id, user_message, response, now), now)`,
   off the request thread, and swallow its errors (a memory failure must never fail a chat turn).
3. **Nightly** (`nightly`, `hybrid` and `hybrid_jev`): schedule `python manage.py personalization_batch`.

**Anonymous to registered.** When the claim endpoint attaches an anonymous session to a signed-in
user, call `store.move(anonymous_user_id, registered_user_id)` in the same transaction. The target's
`stated` slots win, otherwise the newer value wins; observations and proposals move with it, and an
opt-out on either side stays in force.

**Settings and deletion.** `store.set_settings(user_id, personalize_enabled=..., store_enabled=...)`
backs the two toggles; `store.clear(user_id)` deletes slots, observations and proposals in one transaction.

**Concurrency.** Each user has a `generation` that `clear` and `move` bump. A strategy reads it
before a model or Jev call and passes it to its writes; a write from before the bump, or after
`store_enabled` was switched off, is refused (diagnostics `stale_generation`,
`store_disabled_at_commit`). Free-text values are cleaned (no control characters, `<` and `>` become
`‹` and `›`) when validated and again when rendered. A pending
`Proposal` is accepted or rejected with `store.resolve_proposal`; accepting writes a `stated` slot.
