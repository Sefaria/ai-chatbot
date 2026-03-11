"""Teaser Not Spoiler Scorer - Evaluates whether responses entice users to read source texts rather than replacing them. Skips scoring for non-relevant queries."""

NAME = "Teaser Not Spoiler"
SLUG = "teaser-not-spoiler-7e2c"
DESCRIPTION = (
    "Evaluates whether responses entice users to read source texts rather than replacing them. Skips scoring for non-relevant queries."
)

PROMPT = """You are evaluating whether an AI assistant for a Jewish library entices users to read source texts rather than replacing the need to read them.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

Evaluate whether the response teases specific Jewish text sources appropriately—providing enough context to spark interest without substituting for reading the original.

An answer PASSES if it:
- Provides a topic, theme, or compelling reason to explore the source without revealing the core insight or conclusion
- Keeps the response concise, offering a doorway into the text rather than a summary
- Invites curiosity by highlighting what makes the source interesting or relevant
- Provides full text when the user explicitly requests a translation or the text itself
- Provides a summary when the user explicitly requests one

An answer FAILS if it:
- Summarizes a source's main point, argument, or conclusion in a way that removes the need to read it (unless explicitly requested)
- Provides extensive quotes or paraphrases that substitute for the original
- Gives a "book report" style overview that makes reading feel redundant
- Is unnecessarily long when a brief teaser would suffice

Return ONLY the letter of your choice, followed by a summary containing your reasoning.
(a) PASS - the response is appropriate for these criteria
(b) FAIL - the response is not appropriate for these criteria
(c) NOT_RELEVANT - the question is not relevant to what you are evaluating"""
