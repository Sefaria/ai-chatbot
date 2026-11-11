# Labeling brief: dataset anon-impact (prompt relevance and multi-voice labels)

Goal: label one anonymized learner prompt (`id`, `text`) with whether knowing a learner profile changes the best answer (`kind` and `relevant_to`) and whether multiple Jewish sources or authorities offer distinct readings (`many_voices`).

## Ground rules

- Judge only from the prompt text and the slot definitions below.
- Reply with one valid UTF-8 JSON object `{"kind": ..., "relevant_to": ..., "many_voices": ...}` and nothing else.

Slot set, version v2 (copied from slots.json). These are the only legal slot keys for `relevant_to`:

- `preferred_language` (enum): one of ["en", "he", "mixed"]. The language the learner writes in: en, he, or mixed. Judged from the script of their prompts, never from their location.
- `hebrew_fluency` (enum): one of ["none", "basic", "intermediate", "fluent"]. How well the learner reads Hebrew. Evidence: writing Hebrew in prompts, asking for translations of Hebrew, asking what a Hebrew word means. Keep this separate from comfort with study terms: someone can read Hebrew and still not know what a sugya is.
- `terminology_comfort` (enum): one of ["new", "some", "comfortable"]. How comfortable the learner is with Jewish study vocabulary (sugya, gemara, remez, halakhah). Evidence: using such terms without explanation, or asking what they mean. Separate from Hebrew reading ability.
- `register` (enum): one of ["traditional", "academic"]. Whether the learner studies inside the tradition (how the commentators read a source) or in the academic register (dating, context, textual criticism). Set only by code or a typed yes/no or one-of-N answer, never by free-text model consolidation.
- `text_categories` (list): a list of up to 3 from ["Tanakh", "Mishnah", "Talmud", "Midrash", "Halakhah", "Kabbalah", "Chasidut", "Musar", "Liturgy", "Jewish Thought", "Commentary"]. Up to three corpora the learner keeps returning to. Evidence: the books and corpora they ask about or cite. Only recurring interests, not a single question.
- `answer_style` (text): free text, at most 200 characters, second person. How the learner wants answers shaped (length, depth, format, tone), written in the second person and describing the answer, never the person. Example: 'Keep answers short and lead with the source.'
- `learning_habits` (text): free text, at most 200 characters, second person. A study cycle or position the learner is following, in the second person (for example the daf or parashah they are on). Only from repeated evidence across sessions.

## What to label and legal values

Return one JSON object:

```json
{"kind": "relevant", "relevant_to": "terminology_comfort", "many_voices": false}
```

- `kind`: `"relevant"` or `"neutral"`.
  - `"relevant"`: the best answer clearly differs depending on a learner profile slot (for example Hebrew reading level deciding whether to quote Hebrew with translation or English only; terminology comfort deciding whether to define study terms inline; register deciding whether to lead with classical commentators or historical/critical context).
  - `"neutral"`: the best answer does not depend on the learner's profile (for example a plain lookup of a citation, date, name, or factual question).
- `relevant_to`:
  - When `kind` is `"relevant"`: must be one of the 7 slot keys in `slots.json` (`preferred_language`, `hebrew_fluency`, `terminology_comfort`, `register`, `text_categories`, `answer_style`, `learning_habits`).
  - When `kind` is `"neutral"`: must be `null`.
- `many_voices`: boolean (`true` or `false`).
  - `true` if multiple Jewish sources, commentators, or authorities legitimately disagree or offer distinct readings on the question so a good answer presents more than one voice; otherwise `false`.
