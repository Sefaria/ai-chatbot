# Live end-to-end check: the "Joe" story, against a deployed cauldron

Drives a real browser (Playwright, Chromium) against a deployed Sefaria cauldron and a deployed preview chatbot
server, with real model calls and two real throwaway users. 17 steps. It spends model calls (about 25 chat turns
plus Profile-job runs) and takes about 70 minutes, because step 16 waits for the idle sweep. It is skipped unless
`LIVE=1`, and it has its own config, so `npx playwright test` never runs it.

Each step records PASS or FAIL, with the evidence it saw, into a results JSON file and never aborts the run. A step
that fails and passes on a later rerun is "FAILED, THEN RECOVERED" in the report: the file keeps every attempt.

## Prerequisites

- Two existing users with the Library Assistant setting on (sign-up has a reCAPTCHA, so the script cannot create them).
  Step 1 expects user 1 to be fresh ("Last updated: Never"); step 14 expects user 2 to have an empty Profile and Instructions.
- The cauldron may be scaled down; the script waits up to 5 minutes for "Environment is starting up".
- The preview chatbot server runs with personalization on for signed-in users, the Jev gate on, and the 5-minute sweep.

## Run

    LIVE=1 LIVE_USER1_EMAIL=... LIVE_USER1_PASSWORD=... LIVE_USER2_EMAIL=... LIVE_USER2_PASSWORD=... \
      LIVE_SHOTS_DIR=<dir> npx playwright test -c e2e/live/playwright.live.config.js

Read the credentials from mode-600 files into the variables; never put a password on a command line you echo.

Environment variable names:

| Name | Meaning | Default |
|---|---|---|
| `LIVE` | must be set to run | skipped |
| `LIVE_USER1_EMAIL`, `LIVE_USER1_PASSWORD` | user 1, runs the main story | required |
| `LIVE_USER2_EMAIL`, `LIVE_USER2_PASSWORD` | user 2, isolation and keyboard steps | required |
| `LIVE_SEFARIA_URL` | the cauldron site | `https://www.la-p13n.cauldron.sefaria.org` |
| `LIVE_CHATBOT_VERSION` | the `?chatbot_version=` value, opened once per browser context | `9298` |
| `LIVE_CHATBOT_API` | the preview app's API base, for the direct API calls | `https://<version>.ai-server.coolifydev.sefaria.org/api` |
| `LIVE_CHAT_PATH` | the library page hosting the chat | `/Genesis.1` |
| `LIVE_SHOTS_DIR` | screenshots | `test-results/live-shots` |
| `LIVE_RESULTS_FILE` | the results JSON (attempts are appended) | `<shots dir>/results.json` |
| `LIVE_ONLY` | comma list of steps to run, e.g. `3,4,5` (`16a` and `16b` are the two halves of step 16) | all |
| `LIVE_SKIP` | comma list of steps to skip | none |
| `LIVE_TRACE` | file for a timestamped trace of each chat turn | off |
| `LIVE_IDLE_FIRST_LOOK_MIN`, `LIVE_IDLE_GIVE_UP_MIN` | step 16 window after the question | `36`, `46` |

Execution order is 1 to 13, 15, 16a, 14, 17, 16b, then a cleanup test (Clear both Profiles, empty Instructions,
switches back on, sign out). Step 16a signs user 1 out and 16b signs back in after the wait, so user 1 does nothing in
between; steps 14 and 17 use user 2 meanwhile. The last test fails if any recorded attempt failed, so use a fresh
results file for a clean verdict.

## Notes learned from running it

- Opening the chat page in a new chat session can itself start a Profile job for the earlier session (the `new_chat`
  trigger), so a Profile can appear without pressing Run now. Do not open extra chats against user 1 before step 3.
- The first page load of a fresh session with `?chatbot_version=9298` in the URL leaves the Settings panel pointing at the
  default chatbot host (it shows "Personalization is not available here."); the script opens `/about?chatbot_version=...`
  once, then goes to Settings.
- A chat turn can stall: the answer stops after its first "let me search..." line, the input stays disabled, and a page
  reload does not free it. The script records a stall (with a screenshot), then starts a new chat from the menu and asks
  again, and the step ends as "FAILED, THEN RECOVERED". `LIVE_TRACE=<file>` appends a timestamped trace of each turn.
- "Run now" can answer `busy` while a job that a new chat started is still running, and `no_change: no_new_prompts` when
  that job has already processed the prompts; the script presses again after a busy reply and records both.

## Clean re-run suite (`LIVE_SUITE=r`)

`LIVE_SUITE=r` runs steps R0 to R10 (user 1) instead of the 17 numbered steps: a first-load link check, English chat before
any Profile, Run now, the notice, Instructions, two-answer display, chat-to-edit, sensitive content, Hebrew, Clear.
The site allows three questions per chat. The script asks at most two per chat, starts a new chat after that, and treats the
"conversation has reached its limit" message as a normal end state. A turn can show two assistant blocks (a short "let me
check..." line, then the answer), so questions are counted from the user's own messages. A stall is an answer that never
completes (no final answer in 3 minutes, input disabled, no limit message).
