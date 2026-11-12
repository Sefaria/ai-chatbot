# Live end-to-end check: the "Joe" story

Drives a real browser against the real Sefaria site, the real chatbot server and the real model.
It spends model calls (about 20 chat turns plus Profile-job runs). It is skipped unless `LIVE=1`,
and it has its own config, so `npx playwright test` never runs it.

## Start the servers

Chatbot (`server/`, a NEW Postgres database, e.g. `poc_live`; run `manage.py migrate` once):
`DB_NAME=poc_live ENVIRONMENT=dev PERSONALIZATION_OVERRIDE_ENABLED=true PERSONALIZATION_OVERRIDE_ALLOW_ALL=true PERSONALIZATION_LAB_SEED=true`
plus, from your env files, `ANTHROPIC_API_KEY`, `BRAINTRUST_API_KEY`, `BRAINTRUST_PROJECT`, `CHATBOT_USER_TOKEN_SECRET`
(and, for the Jev gate, `PERSONALIZATION_JEV_GATE=on` with `TYPESAFE_API_KEY`).
Load the env file with a loader that handles values with spaces (`set -a; . file` breaks on `BRAINTRUST_PROJECT`).
Then `python manage.py runserver 127.0.0.1:8765 --noreload`.

Widget dev server (repo root): `VITE_SEFARIA_HOSTNAMES=localhost,127.0.0.1 npx vite --port 5173 --strictPort`.

Sefaria (worktree with the Settings tab): in the git-ignored `sefaria/local_settings.py` set
`CHATBOT_API_BASE_URL = "http://127.0.0.1:8765/api"`, `CHATBOT_USE_LOCAL_SCRIPT = True` and a `CHATBOT_USER_ID_SECRET`
equal to the chatbot's `CHATBOT_USER_TOKEN_SECRET`. Run `npm run build-client` (so the bundle has the new tab),
`python manage.py migrate` (the login needs the allauth tables), then `python manage.py runserver 127.0.0.1:8000 --noreload`.
Create a FRESH test user per full run (step 1 expects "Last updated: Never") with its Library Assistant setting on.

## Run

    LIVE=1 LIVE_SEFARIA_USER=<email> LIVE_SEFARIA_PASSWORD=<password> LIVE_SHOTS_DIR=<dir> \
      npx playwright test -c e2e/live/playwright.live.config.js

Env var names: `LIVE`, `LIVE_SEFARIA_URL` (default `http://localhost:8000`), `LIVE_SEFARIA_USER`, `LIVE_SEFARIA_PASSWORD`,
`LIVE_SHOTS_DIR` (screenshots, default `test-results/live-shots`), `LIVE_CHAT_PATH` (page hosting the chat, default `/about`:
reader pages crash on a local Mongo without calendar data). Tests are serial and share state; run one with `-g "<title words>"`.

## Automated: steps 1 to 9

Settings tab, Instructions 301/300, three chat turns with no notice, Run now, notice once, Instructions on/off injection,
two-answer display, chat-to-edit (applied and refused), Clear. Needs a working model key.

## Manual (DB or shell)

- Step 6 last part: Profile switched off, confirm no Profile block reached the prompt (trace metadata or the run/turn record).
- Step 9 end: after Clear, ask a question, Run now, check `ProfileWriterRun.prompt_ids` only holds post-Clear prompts.
- Step 10 idle sweep: `manage.py personalization_sweep --dry-run`, then backdate the prompt's `server_timestamp` (and the user's
  `cleared_at`/`processed_through` if later) 31 minutes and run the sweep twice.
- Steps 11 to 15: Dream now and job health (API), isolation with a second token, sensitive-content check, `ProfileWriterRun` rows,
  Braintrust spans, a Hebrew question.
