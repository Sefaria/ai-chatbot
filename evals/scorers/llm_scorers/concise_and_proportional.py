"""Concise & Proportional Scorer - evaluates whether response length matches question complexity."""

NAME = "Concise & Proportional"
SLUG = "concise-and-proportional-81a9"
DESCRIPTION = (
    "Evaluates whether response length is appropriate for the question complexity"
)

PROMPT = """You are evaluating whether an AI assistant's response length matches the question complexity.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

PASS if:
- Simple questions get concise answers (1-3 paragraphs)
- Complex questions (which are not flagged for safety or redirection) get appropriately detailed responses
- Answer gets to the point without excessive preamble
- All disclaimers are automatic passes

FAIL if:
- Any indication of the tools used or process is present in the response before the body of the response begins. (i.e. "I'll search for more... Let me search... Let me use this tool")
- Lengthy rambling response to a simple question
- Superficial one-liner to a complex analytical question
- Buries the answer in excessive disclaimers or repetition

As a general rule, you want to be concise, so if the answer is simple, we don't need two or three paragraphs. One or two sentences and maybe a source will be enough.

Return ONLY the letter of your choice, followed by a summary containing your reasoning.
(a) PASS - the response is appropriate for these criteria
(b) FAIL - the response is not appropriate for these criteria
(c) NOT_RELEVANT - the question is not relevant to what you are evaluating"""
