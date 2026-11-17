# Labeling brief: dataset anon-personas (real anonymized learner conversations)

Goal: label one anonymized learner's conversation history (`sessions` and `turns`) from a Jewish-texts study chatbot with a golden profile (`golden`), a turn-by-turn timeline of when each slot value becomes supported (`golden_timeline`), and per-turn diagnostic tags (`tags`).

## Ground rules

- Judge only from the conversation turns provided in the input item; never guess or infer from outside knowledge.
- Reply with one valid UTF-8 JSON object and nothing else (no markdown fences, no commentary).
- Every slot key and value must be legal in slot set v2 below.

Slot set, version v2 (copied from slots.json). These are the only legal slot keys; a slot value must be legal as stated:

- `preferred_language` (enum): one of ["en", "he", "mixed"]. The language the learner writes in: en, he, or mixed. Judged from the script of their prompts, never from their location.
- `hebrew_fluency` (enum): one of ["none", "basic", "intermediate", "fluent"]. How well the learner reads Hebrew. Evidence: writing Hebrew in prompts, asking for translations of Hebrew, asking what a Hebrew word means. Keep this separate from comfort with study terms: someone can read Hebrew and still not know what a sugya is.
- `terminology_comfort` (enum): one of ["new", "some", "comfortable"]. How comfortable the learner is with Jewish study vocabulary (sugya, gemara, remez, halakhah). Evidence: using such terms without explanation, or asking what they mean. Separate from Hebrew reading ability.
- `register` (enum): one of ["traditional", "academic"]. Whether the learner studies inside the tradition (how the commentators read a source) or in the academic register (dating, context, textual criticism). Set only by code or a typed yes/no or one-of-N answer, never by free-text model consolidation.
- `text_categories` (list): a list of up to 3 from ["Tanakh", "Mishnah", "Talmud", "Midrash", "Halakhah", "Kabbalah", "Chasidut", "Musar", "Liturgy", "Jewish Thought", "Commentary"]. Up to three corpora the learner keeps returning to. Evidence: the books and corpora they ask about or cite. Only recurring interests, not a single question.
- `answer_style` (text): free text, at most 200 characters, second person. How the learner wants answers shaped (length, depth, format, tone), written in the second person and describing the answer, never the person. Example: 'Keep answers short and lead with the source.'
- `learning_habits` (text): free text, at most 200 characters, second person. A study cycle or position the learner is following, in the second person (for example the daf or parashah they are on). Only from repeated evidence across sessions.

## What to label (output JSON shape)

Return one JSON object with `golden`, `golden_timeline`, and `tags`:

```json
{
  "golden": {
    "preferred_language": {"value": "en"},
    "hebrew_fluency": {"value": "intermediate", "accept": ["basic", "intermediate"]},
    "terminology_comfort": {"value": "comfortable"},
    "register": {"value": "traditional"},
    "text_categories": {"must_include": ["Tanakh"], "must_not_include": []},
    "answer_style": {"any_of": ["short", "source"], "optional": true},
    "learning_habits": null
  },
  "golden_timeline": [
    {"from_turn": "turn-1", "slot": "preferred_language", "value": "en"},
    {"from_turn": "turn-2", "slot": "register", "value": "traditional"}
  ],
  "tags": {
    "turn-1": [],
    "turn-2": ["short_reply"]
  }
}
```

### Legal values

- `golden`:
  - Every key must be one of the 7 slot keys in `slots.json`. A slot with no sufficient evidence in the conversation is `null`.
  - Enum slots (`preferred_language`, `hebrew_fluency`, `terminology_comfort`, `register`): `{"value": "<allowed>", "accept": ["<allowed>", ...]}` (`accept` is optional).
  - List slot (`text_categories`): `{"must_include": ["<allowed>", ...], "must_not_include": ["<allowed>", ...], "any_include": ["<allowed>", ...]}` with values from `allowed` only.
  - Text slots (`answer_style`, `learning_habits`): `{"any_of": ["<keyword>", ...], "optional": true}` or `null`.
- `golden_timeline`:
  - Non-empty list of `{"from_turn": "<turn_id>", "slot": "<slot_key>", "value": <legal_slot_value>}`.
  - `from_turn` must be an existing turn `id` of the input persona.
  - `slot` and `value` must obey `slots.json` (`list` slot takes a non-empty list of up to 3 allowed categories; `enum` slot takes one allowed string; `text` slot takes a string of at most 200 chars).
  - If a slot's value changes at a later turn (for example after an explicit correction), include a second entry with the later `from_turn`.
- `tags` (turn tags):
  - An object mapping each turn `id` to a list of zero or more tags from this closed set:
    `["outlier", "correction", "conflict", "injected", "sensitive", "exclusion", "instruction", "must_not_store", "short_reply"]`.
  - Definitions:
    - `outlier`: an off-pattern prompt that does not reflect persistent learner traits.
    - `correction`: an explicit correction by the learner of an earlier assumption or preference.
    - `conflict`: evidence about a slot that conflicts with an earlier turn and remains unresolved.
    - `injected`: an instruction embedded inside pasted text or a prompt attempting to alter the profile.
    - `sensitive`: private or sensitive personal matter (health, grief, family crisis, distress).
    - `exclusion`: a request to hide or exclude a source, commentator, or tradition (also tag `must_not_store`).
    - `instruction`: an explicit answer-style preference from the learner.
    - `must_not_store`: content that must never be stored in the learner profile.
    - `short_reply`: a reply of at most four words (not the first turn) whose meaning depends on the preceding assistant message.
