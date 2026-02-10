# Safety Improvements Plan

Three changes to increase agent safety: a pre-agent guardrail filter, removal of multi-turn conversations, and a consent opt-in UI.

## Phase 1: Guardrail Checker — DONE

- [x] GuardrailService module (`server/chat/V2/guardrail/`)
- [x] Integrated into agent service with Braintrust child span tracing
- [x] Guardrail rejections flow through normal response path (persisted, summarized)
- [x] Centralized model config in Django settings (AGENT_MODEL, GUARDRAIL_MODEL, SUMMARY_MODEL)
- [x] Centralized user-facing messages in prompt_fragments.py

## Phase 2: Remove Summary / Block Multi-turn — REVERTED

Implemented then reverted to keep PR scope to guardrail only.

## Phase 3: Opt-in Consent UI — REVERTED

Implemented then reverted to keep PR scope to guardrail only.

## Key Decisions

- Guardrail fails closed on any error (Braintrust down, LLM error, malformed JSON)
- Guardrail runs inside agent service (not views) so Braintrust tracing captures it
- Auto-mock guardrail in conftest.py to avoid breaking existing tests
