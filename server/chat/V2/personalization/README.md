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

## Wiring it in

`chat_stream_v2` asks one server-side gate, `gate.resolve(settings, persistent_user_id, requested)`,
what this request may do. With `PERSONALIZATION_MODE=off` and no honoured override the view calls
nothing else in the package and reads no profile table; the prompt is byte-identical to `main`.

- **Settings.** `PERSONALIZATION_MODE`, `PERSONALIZATION_INJECTION` (`always`, default),
  `PERSONALIZATION_OVERRIDE_ENABLED` (only the exact value `true`) and
  `PERSONALIZATION_OVERRIDE_USER_IDS` (comma-separated persistent user ids).
- **Override.** The request `context` may carry `personalizationMode`, `personalizationInjection`,
  `personalizationProfile` (an ephemeral fixture, validated by the registry, rendered for that
  request only, never stored, no after-turn update) and `personalizationSubject` (up to 40 characters
  of `[a-z0-9-]`, which keys the store on `<user>:eval:<subject>`). It is honoured only when the flag
  is on, the `ENVIRONMENT` variable is set and not a production value (`prod`, `production`, `beta`),
  and the user is on the allowlist; otherwise every field is ignored and the trace says
  `personalization_override_refused`. `isStaff` and `labs` never open it.
- **Before the agent runs**, `runtime.profile_for_request` reads the profile (inside a savepoint) and
  sets `profile_text` and `profile_slots_used` on the `MessageContext`. Any failure, a missing table
  included, is reported to Sentry and the turn goes on unpersonalized.
- **After the turn is saved** (never for a stopped, failed or guardrail-refused turn),
  `runtime.submit_after_turn` queues `service.after_turn` on a two-thread pool. Jobs of one learner
  run one at a time in submission order; every exception is swallowed and reported. Each job gets its
  own `RunState` because `collecting()` sets a context variable inside the worker thread.
- **Trace metadata** on the root span (`personalized`, `personalization_mode`, `_injection`, `_source`,
  `_override_refused`, `slots_used`, `slot_set_version`, `profile_revision`, `profile_text`,
  `personalization_model`; with the feature off only `personalized: false` and
  `personalization_source: "off"`). `profile_text` appears only for an honoured override. The update
  runs under a `personalization-update` child span with the turn's `TurnResult` counts.
- **Anonymous to registered.** `anon_claim_v2` calls `store.move` in its own savepoint when the mode
  is not `off` or the override flag is on; a failed move is reported and the claim still succeeds.

Still to do: schedule `python manage.py personalization_batch` for `nightly`, `hybrid` and
`hybrid_jev`; build `hybrid_jev` only where a Jev client is wanted (`runtime.get_service`).

**Move rules.** `store.move(anonymous_user_id, registered_user_id)` is one locked write. The target's
`stated` slots win, otherwise the newer value wins; observations and proposals move with it, and an
opt-out on either side stays in force.

**Settings and deletion.** `store.set_settings(user_id, personalize_enabled=..., store_enabled=...)`
backs the two toggles; `store.clear(user_id)` deletes slots, observations and proposals in one transaction.

**Concurrency.** Every profile write goes through one store helper, `_locked_write`. It takes the
user's lock once (the user's `ProfileSettings` row `FOR UPDATE` in Postgres, a per-user lock in
memory; `move` locks both users in sorted `user_id` order, so two moves cannot deadlock), refuses
with `store_disabled` when the write needs storage consent and it is off (slots, observations,
proposals, accepting a proposal), refuses with `stale_revision` when the caller's
`expected_revision` is not the user's current `revision`, runs the write, and adds 1 to each locked
user's `revision`. Every mutating method returns a `WriteResult(applied, reason, revision)`, which
has no truth value: callers read `.applied`. Other reasons: `duplicate` (an observation for a
`message_id` already logged, or the same pending proposal), `stated_wins` (an inferred write never
replaces a stated slot), `pending_proposal` (an inferred write to a slot awaiting the learner's
confirmation). A public method never calls another public mutating method, so one call bumps the
revision once. The database backs this with unique constraints on `(user_id, message_id)` for
observations and on `(user_id, slot_key)` for pending proposals.

A strategy reads `store.revision(user_id)` once, before its first model or Jev call, and after each
of its own applied writes carries `result.revision` forward as the next `expected_revision`. A
`stale_revision` refusal ends that run for the user: the remaining writes are skipped and the
observations stay pending for the next batch. Every turn starts by logging its observation (a
content-free marker, stored consumed, for `realtime` and `explicit`; a quarantined, empty row for any
turn holding a standing instruction), so a retried `messageId` ends as `duplicate` under every mode. Per-turn
counts (tokens, cost, `writes_applied`, `writes_refused`, `diagnostics`) come back on the
`TurnResult` (and `BatchResult`); the strategy instance keeps no per-turn state, so one instance
serves concurrent turns.

Standing instructions are quarantined by default. `signals.find_standing_instructions` returns every
instruction clause of a message (English and Hebrew; it over-matches on purpose). `hybrid` and
`hybrid_jev` classify each one (`classify_turn`: `SAFE_STYLE`, `EXCLUSION` or `UNKNOWN`; any invalid
reply, missing key or client error is `UNKNOWN`). Only a turn with no instruction, or with all
`SAFE_STYLE`, keeps its text; any other turn stays a `quarantined` row with empty text, hidden from
`store.observations()` unless `include_quarantined=True`, and creates no proposal. `realtime`,
`nightly` and `explicit` have no classifier and quarantine every instruction turn. A proposal records
its `source_message_id`; that observation stays out of inference windows while the proposal is
pending, and rejecting (or replacing) the proposal quarantines it. A consolidation reply that is not
`{"updates": [...], "clear": [...]}` is a failed call (`invalid_envelope`) and consumes nothing.

Free-text values are cleaned (no control characters, `<` and `>` become
`‹` and `›`) when validated and again when rendered. A pending
`Proposal` is accepted or rejected with `store.resolve_proposal`; accepting writes a `stated` slot.
