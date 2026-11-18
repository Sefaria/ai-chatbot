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
