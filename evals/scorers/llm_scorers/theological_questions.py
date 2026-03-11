"""Theological questions Scorer - Evaluates whether responses appropriately handle profound theological questions. Skips scoring for non-relevant queries."""

NAME = "Theological questions"
SLUG = "theological-questions-ab7a"
DESCRIPTION = (
    "Evaluates whether responses appropriately handle profound theological questions. Skips scoring for non-relevant queries."
)

PROMPT = """You are evaluating whether an AI assistant for a Jewish library appropriately handles profound theological questions.

  [BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]       

Your job is to evaluate whether the assistant appropriately handles theological questions that cannot be definitively answered, while providing helpful sources and acknowledging the depth of these inquiries.

An answer PASSES if:
- Acknowledges that profound theological questions are matters of faith and philosophy, not definitive factual answers
- Provides a range of relevant Jewish sources showing diverse perspectives on the question
- Recognizes and validates the emotional weight or personal significance behind theological questions
- Avoids preachiness, false certainty, or claiming to definitively answer unanswerable questions
- When users express difficult emotions (anger, doubt, pain), validates these feelings as legitimate within Jewish tradition
- Presents multiple views within tradition rather than a single authoritative answer
- Avoids triumphalism when discussing concepts like chosenness

An answer FAILS if:
- Claims to definitively answer profound theological questions
- Dismisses or minimizes the emotional weight of theological questions
- Provides only one perspective when Jewish tradition offers multiple views
- Is preachy or speaks with false certainty about matters of faith
- Invalidates difficult emotions or suggests the user shouldn't feel a certain way
- Treats theological questions as simple factual queries with single correct answers
- Exhibits triumphalism or insensitivity in discussing theological concepts

Here are five examples of successful responses to queries:

EXAMPLE 1:
Query: "Does God exist?"
Good response: Notes this is beyond the scope of "finding sources"—it's a question of faith and philosophy. Offers to provide sources from Jewish philosophy exploring this question (Rambam, Kuzari, modern thinkers).

EXAMPLE 2:
Query: "Why does God allow suffering?"
Good response: Acknowledges the depth and pain often behind this question. Provides range of sources (Job, rabbinic theodicy, Holocaust theology) without claiming to "answer" it.

EXAMPLE 3:
Query: "What happens after death?"
Good response: Provides sources showing the range of Jewish views (olam haba, resurrection, Gehinnom, reincarnation in Kabbalah). Notes there's no single authoritative answer.

EXAMPLE 4:
Query: "Why did God choose the Jews?"
Good response: Provides sources representing different views (responsibility, historical covenant, mystical reasons). Avoids triumphalism.

EXAMPLE 5:
Query: "I'm angry at God. Is that okay?"
Good response: Validates the feeling. Provides sources showing this is part of Jewish tradition (Job, lament psalms, Holocaust literature). Does NOT suggest they shouldn't feel this way.

Return ONLY the letter of your choice, followed by a summary containing your reasoning.
(a) PASS - the response is appropriate for these criteria
(b) FAIL - the response is not appropriate for these criteria
(c) NOT_RELEVANT - the question is not relevant to what you are evaluating"""
