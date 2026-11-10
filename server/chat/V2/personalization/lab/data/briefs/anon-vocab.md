# Labeling brief: dataset anon-vocab (study-vocabulary register labels)

Goal: label one anonymized learner message (`id`, `text`) with the study-vocabulary register (`label`) it shows.

## Ground rules

- Judge only from the text of the message provided; never guess from outside context.
- Reply with one valid UTF-8 JSON object `{"label": "<value>"}` and nothing else.

## What to label and legal values

Return `{"label": "<value>"}` where `<value>` is exactly one of the five legal labels:

- `traditional`: study inside the tradition: how the Rishonim and Acharonim read a source, mesorah, "what does Rashi say", halakhic ruling, without academic framing.
- `academic`: the critical register: dating, redaction, manuscripts, source criticism, historical context, comparison with the ancient Near East.
- `transliterated`: Hebrew or Aramaic study terms spelled in Latin letters ("sugya", "pshat", "remez", "daf", "mishnah") in otherwise English text, with neither a classical-commentator nor an academic frame.
- `yeshivish`: the Beit Midrash idiom: "a kasha on the Rashba", "lomdus", "chavruta", "the svara", "I was machria", Hebrew and Yiddish-flavoured English or Hebrew.
- `none`: no study vocabulary at all; plain everyday words.

Slot set, version v2 (copied from slots.json), for reference. This dataset labels vocabulary register (`label`); the `register` and `terminology_comfort` slots are the profile slots these labels inform:

- `preferred_language` (enum): one of ["en", "he", "mixed"]. The language the learner writes in: en, he, or mixed. Judged from the script of their prompts, never from their location.
- `hebrew_fluency` (enum): one of ["none", "basic", "intermediate", "fluent"]. How well the learner reads Hebrew. Evidence: writing Hebrew in prompts, asking for translations of Hebrew, asking what a Hebrew word means. Keep this separate from comfort with study terms: someone can read Hebrew and still not know what a sugya is.
- `terminology_comfort` (enum): one of ["new", "some", "comfortable"]. How comfortable the learner is with Jewish study vocabulary (sugya, gemara, remez, halakhah). Evidence: using such terms without explanation, or asking what they mean. Separate from Hebrew reading ability.
- `register` (enum): one of ["traditional", "academic"]. Whether the learner studies inside the tradition (how the commentators read a source) or in the academic register (dating, context, textual criticism). Set only by code or a typed yes/no or one-of-N answer, never by free-text model consolidation.
- `text_categories` (list): a list of up to 3 from ["Tanakh", "Mishnah", "Talmud", "Midrash", "Halakhah", "Kabbalah", "Chasidut", "Musar", "Liturgy", "Jewish Thought", "Commentary"]. Up to three corpora the learner keeps returning to. Evidence: the books and corpora they ask about or cite. Only recurring interests, not a single question.
- `answer_style` (text): free text, at most 200 characters, second person. How the learner wants answers shaped (length, depth, format, tone), written in the second person and describing the answer, never the person. Example: 'Keep answers short and lead with the source.'
- `learning_habits` (text): free text, at most 200 characters, second person. A study cycle or position the learner is following, in the second person (for example the daf or parashah they are on). Only from repeated evidence across sessions.
