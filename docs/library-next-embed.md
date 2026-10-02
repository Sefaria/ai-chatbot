# Embedding the Library Assistant in Library Next

How the Library Next host (Sefaria-Project `mf3`, `static/js/library-next/`) embeds
`<lc-chatbot>` with a persona, how `persona` flows through the stack, and what is only
simulated. Branch: ai-chatbot `mf3`.

## Host markup

```html
<script src="https://<chatbot-host>/static/js/lc-chatbot.umd.cjs"></script>

<lc-chatbot
  user-id="<encrypted user token from Sefaria>"
  api-base-url="https://<chatbot-host>/api"
  mode="panel"
  origin="library-next"
  interface-lang="en"        <!-- or "he" -->
  persona="learner"          <!-- newcomer | learner | educator | scholar -->
  is-moderator="false"
></lc-chatbot>
```

| Attribute | Value | Notes |
|---|---|---|
| `user-id` | encrypted token | Required. Same token the classic site passes (`chatbot_user_token`). |
| `api-base-url` | `https://<chatbot-host>/api` | Required. Must match the host that served the bundle (see Deploying). |
| `persona` | `newcomer` \| `learner` \| `educator` \| `scholar` | Optional. Anything else is ignored (widget behaves as if unset). |
| `origin` | `library-next` | Tags Braintrust traces so Library Next traffic can be filtered. |
| `interface-lang` | `en` \| `he` | Widget UI language, including the starter prompts. |
| `mode` | `panel` \| `floating` | `panel` for the dock. |
| `is-moderator` | `true` \| `false` | Host sets it from `request.user.is_staff`. |

### Programmatic send and inline mode

- `mode="panel"` renders the chat inline, filling the `<lc-chatbot>` element (the dock owns the
  side panel, open/close and sizing). Older bundles (chat-dev) do not know this mode and fall
  back to the floating corner widget.
- `initial-prompt="<text>"`: each new non-empty value opens the chat and sends it; clear the
  attribute afterwards (`removeAttribute`) so the same text can be sent again. Feature-detect
  with `'initial-prompt' in el` (false on chat-dev bundles; the dock then drives the widget's
  shadow-DOM textarea and send button as a compatibility fallback).

### Changing persona at runtime

`persona` is an observed attribute. The dock should call
`el.setAttribute('persona', newPersona)` whenever `usePersona()` changes; the widget
re-derives the starter prompts and sends the new value on the next request. Removing the
attribute (or setting an unknown value) returns the widget to persona-less behaviour.
No reload or re-mount is needed.

## How `persona` flows

```
<lc-chatbot persona="scholar">
  └─ src/components/LCChatbot.svelte   persona prop → $derived, validated against the enum
       ├─ empty state: 3 starter prompts per persona (assistant.starter.<persona>.{1,2,3})
       │    buttons carry data-feature-name="starter_prompt" (GA4 assistant_click, link_text = prompt)
       └─ src/lib/api.js                context.persona on POST /api/v2/chat/stream
            └─ server/chat/serializers.py        ChoiceField(PERSONAS); unknown value → 400 (widget never sends one)
                 ├─ server/chat/V2/persona.py     resolve_persona() → str | None
                 ├─ MessageContext.persona        carried through the agent layer
                 ├─ trace_logger.py               Braintrust span metadata.persona
                 └─ prompt_fragments.build_prompt core prompt → "Reader profile:" guidance → summary → page → turn
```

The Anthropic-compatible endpoint (`POST /api/v2/chat/anthropic`, used by Braintrust evals)
takes the same value in `metadata.persona`.

Persona guidance text lives in `server/chat/V2/prompts/prompt_fragments.py::PERSONA_GUIDANCE`.
In short: newcomer = define terms, no assumed Hebrew, suggest where to start; learner =
active recall, encouragement, connect to the current page; educator = discussion questions,
age-appropriate framing, source-sheet-ready formatting; scholar = versions and manuscripts,
textual variants, academic register.

## Deploying a preview of this branch

The chatbot backend and bundle are one Coolify application (`sefaria/ai-chatbot:server`,
built from `Dockerfile`, which bundles the widget into `/static/js/`). Coolify builds a
**preview per pull request**, not per branch:

- URL: `https://<PR#>.ai-server.coolifydev.sefaria.org` (bundle at
  `/static/js/lc-chatbot.umd.cjs`, API at `/api`). The Coolify bot comments on the PR when
  it is ready; if there is no comment, deploy it from Coolify → *Preview deployments*.
- Sefaria-Project selects it with `?chatbot_version=<PR#>` (integer only; stored in the Django
  session, cleared with `?chatbot_version=clear`). `sefaria/system/context_processors.py`
  builds the script URL and `ReaderApp.jsx` the `api-base-url` from it. Reload once after
  setting it — the first page load still uses the default backend.
- Pushing `mf3` **without a PR produces no preview**. `main` → `chat-dev.sefaria.org`,
  `production` → `chat.sefaria.org` via `.github/workflows/release.yaml` (semantic-release +
  AppliedAI-Infrastructure deploy); `nixpacks.toml` is a frontend-only static build and is not
  what serves `ai-server`.

Without a preview, the cauldron uses the default backend (`chat-dev`), which ignores
`context.persona` (the serializer there has no such field, but DRF drops unknown keys, so
requests still succeed). The widget still works and the starter prompts still show; only
the prompt guidance and trace tagging are missing.

## What is simulated

- Nothing on the chatbot side is faked: persona changes the prompt and is tagged on the trace. It is
  request-scoped (no DB column), so per-persona analysis uses Braintrust `metadata.persona`.
- Starter prompts are static strings per persona and language (Weblate-managed), not
  generated from the page or the user's history.
- Library Next's own "AI-generated discussion questions" are templates on the host side; the
  assistant is the real path for generated questions (educator persona).
