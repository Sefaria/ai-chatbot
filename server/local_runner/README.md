# Local agent runner

Runs the chatbot's agent loop on your own machine, against your own Claude
subscription, while the chat UI stays on sefaria.org.

Design and decisions: [`docs/plans/2026-09-08-local-agent-runner.md`](../../docs/plans/2026-09-08-local-agent-runner.md).

## What it is

A Django host that reuses the chat app's own views over local SQLite, so the SSE
contract, cancellation and recovery paths are the production ones rather than a
reimplementation. What it replaces is only what needs our secrets: prompts, the
guardrail and the router are proxied to our server, which holds the Braintrust
key and runs the real services.

> [!IMPORTANT]
> The `/api/v2/local/*` endpoints this runner depends on are **not deployed to
> `chat.sefaria.org` yet** — they are on this branch. Until it ships, point
> `SEFARIA_CHATBOT_URL` at a server you are running locally.

## Prerequisites

1. **Claude Code, logged in.** The runner spends your subscription through the
   `claude` CLI:
   ```bash
   claude login
   ```
   If the CLI's OAuth session has expired the agent fails with
   `Failed to authenticate: OAuth session expired`. Re-run `claude login`.

2. **Dependencies:**
   ```bash
   pip install -r server/requirements-local.txt
   ```

## Running it

```bash
cd server && python -m local_runner
```

It migrates its own SQLite database, prints a pairing code, and serves on
`http://127.0.0.1:8899`.

```
Sefaria agent runner
  data:     ~/.sefaria-agent
  database: ~/.sefaria-agent/local.sqlite3
  serving:  http://127.0.0.1:8899

  To connect this machine, open sefaria.org and enter:
      418302

  Valid once, for 10 minutes. Restart to get a new one.
```

## Pairing a browser

Open sefaria.org, open the chat so the widget knows who you are, then in the
browser console:

```js
await window.sefariaLocalMode.pair('418302')   // the code from the terminal
```

Reload. The widget probes the runner on load and switches to it automatically
from then on.

```js
await window.sefariaLocalMode.status()      // { available, paired, active }
window.sefariaLocalMode.disconnect()        // forget the token, back to the server
```

> A first-run pairing surface belongs in the settings panel. The console entry
> point keeps the beta usable without shipping UI that has not been designed.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SEFARIA_AGENT_HOME` | `~/.sefaria-agent` | Data directory (database, pairing record) |
| `SEFARIA_AGENT_PORT` | `8899` | Port to serve on |
| `SEFARIA_CHATBOT_URL` | `https://chat.sefaria.org` | Server to proxy prompts, guardrail, router and summaries to |
| `SEFARIA_ALLOWED_ORIGINS` | *(none)* | Extra origins allowed to reach the runner, comma separated. Needed to test against a local build, e.g. `http://localhost:5173` |

## What is different from the hosted agent

| | Hosted | Local |
|---|---|---|
| Agent tokens | Our API key | Your Claude subscription |
| Conversation history | Postgres, cross-device | Local SQLite, this machine only |
| Prompts, guardrail, router | Direct | Proxied to our server |
| Conversation summaries | LLM-generated | Proxied — same summaries |
| Topic appetizer | Yes | **Disabled** — it needs its own Anthropic key |
| Braintrust traces | Written directly | **Not sent** |

The last two are known gaps. Neither affects the turn: the appetizer is a topic
chip the UI already treats as optional, and traces are observability rather than
behaviour.

Summaries are proxied rather than dropped because the agent is handed only the
current message — the summary carries the entire multi-turn memory, so without it
local mode would quietly become single-turn.

## Security

The daemon listens on loopback, which any page in your browser can reach, and it
spends your subscription. So:

- Every route requires the runner token pairing produced. `/health` and `/pair`
  are the only exceptions.
- The pairing code is single use, expires after 10 minutes, and is discarded
  after 5 wrong guesses. It only ever lives in memory.
- `ALLOWED_HOSTS` is loopback-only, which is what rejects DNS rebinding.
- Cross-origin requests are refused unless they come from sefaria.org.
- The runner holds **no** server secrets: no Braintrust key, no user-token
  secret, no database credentials.
- The agent gets the same Sefaria tools as the hosted one. No filesystem or shell
  tools, by design.

## Troubleshooting

**`Failed to authenticate: OAuth session expired`** — run `claude login`.

**Every message is blocked with "I can't check that message right now"** — the
runner could not reach the guardrail on our server. Check `SEFARIA_CHATBOT_URL`.
The guardrail fails closed on purpose: an unchecked message is not an allowed
one.

**The widget never switches to local mode** — check
`await window.sefariaLocalMode.status()`. `available: false` means the browser
could not reach the runner; `paired: false` means pair again.

**Pairing says the code expired** — codes are single use and last 10 minutes.
Restart the runner for a new one. A failed attempt does not burn the code: only a
pairing that actually completes spends it.

**The widget stays on the server when testing a local build** — the runner only
accepts sefaria.org origins by default. Set
`SEFARIA_ALLOWED_ORIGINS=http://localhost:5173`.

## Testing against a local stack

```bash
# 1. the chatbot server (needs a valid ANTHROPIC_API_KEY and BRAINTRUST_API_KEY)
cd server && python manage.py runserver 127.0.0.1:8001

# 2. the runner
SEFARIA_CHATBOT_URL=http://127.0.0.1:8001 \
SEFARIA_ALLOWED_ORIGINS=http://localhost:5173 \
  python -m local_runner

# 3. the widget
npm run dev
```

The demo page generates its own `user-id`, which the server will reject as an
invalid token. Set a validly minted one on the element to pair:

```js
document.querySelector('lc-chatbot').setAttribute('user-id', '<minted token>')
```
