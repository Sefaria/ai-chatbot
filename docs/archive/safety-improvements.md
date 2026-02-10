# Safety Improvements Plan

Three changes to increase agent safety: a pre-agent guardrail filter, removal of multi-turn conversations, and a consent opt-in UI.

## Phase 1: Guardrail Checker — DONE

- [x] GuardrailService module (`server/chat/V2/guardrail/`)
- [x] Shared pre-flight checks (`server/chat/V2/checks.py`)
- [x] Integrated into both endpoints (views.py, anthropic_views.py)
- [x] Frontend guardrail SSE event handling

## Phase 2: Remove Summary / Block Multi-turn — DONE

- [x] Removed SummaryService from request path
- [x] Deleted `summary_service.py` (kept ConversationSummary DB model)
- [x] Removed `summary_text` from MessageContext and build_system_prompt
- [x] Multi-turn rejection via shared pre-flight checks (turn_count >= 1)
- [x] Frontend "conversation complete" UI with new conversation button

## Phase 3: Opt-in Consent UI — DONE

- [x] Consent storage key in localStorage
- [x] Two-checkbox consent panel before chatbot use
- [x] "Why am I seeing this?" expandable section
- [x] Persisted in localStorage, reappears on clear

## Key Decisions

- Guardrail fails closed on any error (Braintrust down, LLM error, malformed JSON)
- Multi-turn check runs before guardrail to avoid unnecessary LLM calls
- ConversationSummary DB model preserved for existing data
- Auto-mock guardrail in conftest.py to avoid breaking existing tests
