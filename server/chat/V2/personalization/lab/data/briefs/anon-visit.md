# Labeling brief: dataset anon-visit (first-visit reply language labels)

Goal: label one anonymized first-visit item (`id`, `site`, `browser_lang`, `country`, `prompt`) with `expected_reply_lang`, the language the assistant's reply should use.

## Ground rules

- The expected reply language follows the language the learner wrote `prompt` in, judged from its script, never from `site`, `browser_lang`, or `country`.
- Reply with one valid UTF-8 JSON object `{"expected_reply_lang": "<value>"}` and nothing else.

## What to label and legal values

Return `{"expected_reply_lang": "<value>"}` where `<value>` is one of `["he", "en", "mixed"]`:

- `he`: the prompt is written in Hebrew script.
- `en`: the prompt is written in English (Latin script).
- `mixed`: the prompt clearly mixes both Hebrew and English scripts. A prompt written wholly in one script must be labeled with that script's language (`he` or `en`).

Slot set, version v2 (copied from slots.json), for reference. `preferred_language` (`en`, `he`, `mixed`) is the slot this label corresponds to:

- `preferred_language` (enum): one of ["en", "he", "mixed"]. The language the learner writes in: en, he, or mixed. Judged from the script of their prompts, never from their location.
- `hebrew_fluency` (enum): one of ["none", "basic", "intermediate", "fluent"]. How well the learner reads Hebrew. Evidence: writing Hebrew in prompts, asking for translations of Hebrew, asking what a Hebrew word means. Keep this separate from comfort with study terms: someone can read Hebrew and still not know what a sugya is.
- `terminology_comfort` (enum): one of ["new", "some", "comfortable"]. How comfortable the learner is with Jewish study vocabulary (sugya, gemara, remez, halakhah). Evidence: using such terms without explanation, or asking what they mean. Separate from Hebrew reading ability.
- `register` (enum): one of ["traditional", "academic"]. Whether the learner studies inside the tradition (how the commentators read a source) or in the academic register (dating, context, textual criticism). Set only by code or a typed yes/no or one-of-N answer, never by free-text model consolidation.
- `text_categories` (list): a list of up to 3 from ["Tanakh", "Mishnah", "Talmud", "Midrash", "Halakhah", "Kabbalah", "Chasidut", "Musar", "Liturgy", "Jewish Thought", "Commentary"]. Up to three corpora the learner keeps returning to. Evidence: the books and corpora they ask about or cite. Only recurring interests, not a single question.
- `answer_style` (text): free text, at most 200 characters, second person. How the learner wants answers shaped (length, depth, format, tone), written in the second person and describing the answer, never the person. Example: 'Keep answers short and lead with the source.'
- `learning_habits` (text): free text, at most 200 characters, second person. A study cycle or position the learner is following, in the second person (for example the daf or parashah they are on). Only from repeated evidence across sessions.
