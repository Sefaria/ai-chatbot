# Anonymized real-data pull

Reads a local export of learner turns, drops staff and sensitive turns, and writes four datasets
outside any git repository. Ids become fresh pseudonyms. The mapping stays in memory for the
run and is never written. This code makes no network call. The export is produced separately.

## Fields file

A JSON object maps logical fields to dotted paths in each row. `L/real/fields.json` is the
production mapping (paths only). `fields.example.json` is an invented illustration.

| Key | Required | Meaning |
|---|---|---|
| `user_id`, `conversation_id`, `timestamp`, `user_text`, `assistant_text` | yes | dotted paths |
| `staff_marker` | yes | `{"path", "staff_values", "rule"}`. Missing: the pull refuses and does not guess a filter |
| `site`, `browser_lang`, `country` | no | dotted path, or `null` when the export has no such field |

`rule` is `drop_user_if_any`: a user with any staff-marked row is dropped entirely. A null marker
is not staff.

`user_text` and `assistant_text` may be a string, an object with a string `content`, or an object
with a `messages` list. From a list, the user side keeps the last message whose role is `user`,
and the assistant side keeps the last message whose role is `assistant`. Anything else is
unreadable, dropped, and counted.

A null `site`, `browser_lang`, or `country` still allows the pull. `anon-visit` is then written
with `meta.json` `"blocked"` naming each missing field, and those keys are left off the items.

## How to run

The output directory, and the export file, must not be inside a git repository
(`$LA_LAB_DATA_DIR` qualifies).

```bash
python -m chat.V2.personalization.lab.real.pull \
  --fields server/chat/V2/personalization/lab/real/fields.json \
  --export "$LA_LAB_DATA_DIR"/export.json \
  --min-turns 4 \
  --max-turns 8 \
  --target-users 200 \
  --workers 4 \
  --out "$LA_LAB_DATA_DIR"
```

`--workers N` (default 4) runs the name pass over all texts in a thread pool; output order and
contents are the same as `--workers 1`.

The export is a JSON list of rows, or an object `{"data": [rows]}`.

The pull keeps non-staff learners who have between `--min-turns` and `--max-turns` readable user
turns (inclusive), at most `--target-users` of them, and keeps each assistant reply with its user
turn. Sensitive turns are dropped with the existing keyword screen before any model sees them.

Under `--out`:

- `anon-personas/users.jsonl` — pseudonymous `user_id`, pseudonymous `sessions[].session_id`,
  and `turns[]` with `at`, `user`, `assistant` (plus a pseudonymous turn `id`)
- `anon-vocab/items.jsonl`, `anon-impact/items.jsonl` — one object per kept user prompt, `id` and `text`
- `anon-visit/items.jsonl` — `id` and `prompt` (the key the validator reads), plus `site`, `browser_lang`, and `country` when those paths are mapped

Each directory also gets `summary.json` (counts and shares only), `meta.json`
(`anonymized` true only when the residual scan finds nothing, `name_pass_failures` is 0, and the dataset has at least one row),
and `sha256` over those files.

## Scrub

On user and assistant text, in order: email addresses become `[email]`, URLs that carry a query
string become `[url]`, phone numbers become `[phone]`, digit runs of 6 or more become `[number]`.
A headless Claude pass (`ClaudeCodeLLMClient`, which already uses `claude -p --no-session-persistence`)
returns exact spans that are names of private persons. Those spans become `[name]`. Rabbis,
biblical and historical figures, authors, and book titles are not requested. A call that raises or returns a reply that is not
a JSON list of exact spans is retried up to 2 more times (3 attempts, `max_tokens` 1000); if all
fail it counts as a failure for that text, tallied as `name_pass_failures` in every `summary.json`; the deterministically scrubbed text
is still kept.

The residual scan runs those same four patterns over the scrubbed user and assistant text.
`meta.json` sets `"anonymized": true` only when that count is 0, no name-pass text failed, and the dataset is not empty.

## Known limits

The sensitive screen is a keyword list. It can drop an ordinary study turn and it cannot report
the sensitive turns it misses. The name pass can miss a private name, or mark a span that is not
one; a miss is not visible to the residual scan, which only repeats the four deterministic patterns.
Pseudonyms are random per pull (`secrets.token_hex`); they are not a stable hash of the source id.
