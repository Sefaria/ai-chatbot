# Profile and Instructions personalization (phase-1 POC, lab deployments only)

A signed-in user gets a Profile LA writes from their own prompts and Instructions they type; both
are used in answers and managed from Sefaria's Settings. Everything sits behind `gate.py`:
`PERSONALIZATION_OVERRIDE_ENABLED`, `PERSONALIZATION_OVERRIDE_ALLOW_ALL`, a non-production
`ENVIRONMENT`, and a signed-in (non-`anon:`) user id.

| Module | Holds |
|---|---|
| `core_slots.py` | The eight core Profile slots |
| `profile_store.py` | All reads and writes of settings, sections and writer runs |
| `settings_api.py` | The settings endpoint (skeleton) |
| `runtime.py` | What a turn gets from personalization; the background queue |
| `quota.py` | Job cost limits and single-flight locks (`LabQuota`, `LabJobLock`) |
| `jev.py`, `llm.py`, `parsing.py`, `signals.py` | Jev client, model clients, JSON and text helpers, the sensitive screen |

Prompt chapters live in `chat/V2/prompts/chapters/` and load through `load_chapter(name)`.
The anonymized real-data loader (for writer evidence) is under `tests/support/`.
