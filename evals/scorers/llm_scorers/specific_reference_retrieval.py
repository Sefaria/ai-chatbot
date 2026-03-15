"""Specific Reference Retrieval Scorer - Evaluates whether the AI correctly retrieves and displays the specific text reference requested. Skips scoring for non-relevant queries."""

NAME = "Specific Reference Retrieval"
SLUG = "specific-reference-retrieval-e8c4"
DESCRIPTION = (
    "Evaluates whether the AI correctly retrieves and displays the specific text reference requested. Skips scoring for non-relevant queries."
)

PROMPT = """First, determine if this query requests a specific Jewish text reference (e.g., "Show me Genesis 1:1", "Get me Berakhot 2a", "What does Rashi say on Exodus 3:14?").

If the query does NOT request a specific text reference retrieval, return ONLY:
(c) NOT_RELEVANT

If the query DOES request a specific text reference, evaluate the response:

You are evaluating whether an AI assistant correctly retrieved a specific Jewish text reference from the Sefaria library.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

Evaluation Criteria:

An answer PASSES if:
- The AI retrieved the exact reference requested (correct book, chapter, verse/section)
- The text content matches what was requested
- The reference is accurately cited

An answer FAILS if:
- The wrong reference is retrieved (incorrect book, chapter, or verse)
- The text content doesn't match the requested reference
- The reference is missing or inaccurate
- The AI retrieves a different reference than what was requested

Return ONLY the letter of your choice, followed by a summary containing your reasoning.
(a) PASS - the AI correctly retrieved the specific reference
(b) FAIL - the AI did not correctly retrieve the specific reference
(c) NOT_RELEVANT - the query does not request a specific text reference"""
