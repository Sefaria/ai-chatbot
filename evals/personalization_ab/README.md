# Personalization A/B harness

Builds two user Profiles from the same ten turns with the real profile writer
(`chat.V2.personalization.profile_job.run_profile_job`):

- **A** adds the turns one at a time and runs the session-window job after each.
- **B** starts from an empty Profile, adds all ten, and runs the nightly "dream" job once.

`select.py` dedupes repeated prompts and picks users (seeded, at least 20 turns each).
`profiles.py` runs the builds; quota is switched off inside the build functions only, and
nothing under `server/` is changed.

## Tests

Run from `server/` (the Django settings and database come from there), always with the test DB name:

    source server/venv/bin/activate
    cd server
    DB_NAME=ai_chatbot_test_t8a pytest --ds=chatbot_server.test_settings_postgres ../evals/personalization_ab/tests -q

`evals/` is outside the pytest testpaths, so `tests/conftest.py` repeats the autouse guardrail mock
from `server/conftest.py`. The tests use a fake model, a fake Jev client and invented text.

## Real data

Real user prompts, answers and built Profiles never go in git. Keep them in a gitignored or
out-of-repo location; only invented text belongs in tests and fixtures.

## Second half: answers, judges, report, CLI

- `answer.py` `answer_turn(user_id, history, prompt, profile_text, runner=None)` runs the real
  `ClaudeAgentService.send_message` in process. **How history reaches the pipeline:** `history`
  (a list of `Turn`) becomes alternating `ConversationMessage` user/assistant pairs (the turn's prompt
  and its original, scrubbed answer) ahead of the new prompt, and `format_conversation` serialises all
  of them. Production sends only the latest message plus a session summary; the harness sends a short
  explicit history instead, and sends the identical list for the A and B runs of a prompt. The Profile
  goes in `MessageContext.profile_text`, the field the production view fills; `instructions_text` is
  empty. The harness uses the last 4 turns before the prompt as history.
- `judge.py` `run_judges(packet, models, seed, call=None)`: opus, sonnet and haiku each judge alone via
  `claude -p --model <m> --system-prompt ... --tools "" --strict-mcp-config --disable-slash-commands
  --setting-sources "" --settings '{}' --output-format json --no-session-persistence`, with
  `MAX_THINKING_TOKENS=0`, stdin closed and a throwaway cwd (no tools, no settings, no thinking).
  Judgments: Profile accuracy for A and B (1-5 with supported / missed / invented), A vs B overall, and for
  each of prompts 11-20 a ranking of original, A, B shown blind as labels 1-3 in the order
  `random.Random(seed + prompt_index)` gives. The prompt text is in the `PROFILE_SYSTEM` and
  `RANKING_SYSTEM` constants.
- `report.py` `log_experiment(packets, judgments, prompts_mode, client=None)` logs a Braintrust experiment
  in "On Site Agent": one row per (user, prompt 11-20) and one row per user for the Profile judgments.
  Metadata holds `prompt_versions` (`chapter_source()` when it exists, else "bundled"), `prompts_mode` and
  the seed.
- `run_ab.py --users N --seed S --data-dir DIR --prompts bundled|braintrust [--dry-run]`. A data dir
  inside a git repository exits 2. `--dry-run` only checks the arguments and the dir. A real run needs
  Django settings and a database (use a throwaway `DB_NAME`), plus API keys, and writes packets to
  `DIR/pilot/` (3 users or fewer) or `DIR/full/` as `user-<pseudonym>.json`.
  `--prompts` is recorded in the report; the core prompt itself still comes from the prompt service.

`DIR/export.json` is a JSON list of objects: `user_id`, `session_id`, `timestamp` (ISO 8601),
`prompt`, `answer`. Users with fewer than 20 turns after dedupe are not picked. Prompts and answers go
through `real_scrub` (URLs, emails, phones, long numbers, private names) before anything else uses them;
a user whose scrub leaves residue is skipped.
