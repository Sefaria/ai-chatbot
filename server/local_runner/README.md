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

2. **Python 3.11 or newer.** `datetime.UTC` and other stdlib names used here do
   not exist earlier. If `python` on your PATH is older (a pyenv default, say),
   use the server virtualenv explicitly:
   ```bash
   ./venv/bin/python -m local_runner
   ```

3. **Dependencies:**
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

Open sefaria.org, open the chat, then in the settings panel (gear → **Agent
Settings**):

1. Tick **Labs**. The local runner section only appears with labs on.
2. Tick **Run against my local runner**.
3. Enter the code from the terminal and press **Connect**.

The widget switches immediately, and re-detects the runner on every load from
then on. **Disconnect** forgets the token and returns to the server.

Local mode is gated on labs both ways: with labs off the setting is hidden *and*
the runner is not used, so the running state never outlives the control that
governs it.

The same operations are available from the console for scripting:

```js
await window.sefariaLocalMode.status()      // { available, paired, active }
await window.sefariaLocalMode.pair('418302')
window.sefariaLocalMode.disconnect()
```

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

**`ImportError: cannot import name 'UTC' from 'datetime'`** — you are on Python
3.10 or older. Run it with `./venv/bin/python -m local_runner`.

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

**"No runner found" while the runner is clearly running** — check the runner's
log for `Refused a request from <origin>`. The runner accepts sefaria.org and
sefaria.org.il by default; any other origin, including a local build, needs
`SEFARIA_ALLOWED_ORIGINS=http://localhost:5173`. The settings panel reports this
case separately from a missing runner.

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
