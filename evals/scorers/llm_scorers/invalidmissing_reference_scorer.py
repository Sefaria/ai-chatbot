"""Invalid/Missing Reference Scorer Scorer - Evaluates whether responses handle invalid, missing, or malformed references appropriately without hallucinating. Skips scoring for non-relevant queries."""

NAME = "Invalid/Missing Reference Scorer"
SLUG = "invalidmissing-reference-scorer-b7ce"
DESCRIPTION = (
    "Evaluates whether responses handle invalid, missing, or malformed references appropriately without hallucinating. Skips scoring for non-relevant queries."
)

PROMPT = """You are evaluating whether an AI assistant for a Jewish library handles invalid, missing, or malformed references appropriately without hallucinating content.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

Evaluate whether the response gracefully handles a reference that doesn't exist or cannot be found in the library.

An answer PASSES if it:
- Clearly acknowledges that the reference cannot be found or is invalid
- Avoids inventing or hallucinating text that doesn't exist
- Maintains a helpful tone while explaining the limitation
- Offers constructive next steps (e.g., suggests similar sources, asks for clarification, or checks if the reference might be formatted differently)
- Accurately explains why the reference might be problematic (e.g., "not yet in our library," "might be a typo," "unclear format")
- Decided to give references that weren't specifically requested or refused to answer the question

An answer FAILS if it:
- Invents or fabricates text claiming it's from the requested reference
- Pretends to have access to sources it doesn't have
- Provides content from a different reference without clearly stating the reference is unavailable
- Gives a vague or unhelpful response that doesn't acknowledge the specific problem
- Hallucinates details about why the source exists or what it contains
- Says Sefaria doesn't have sources it actually DOES have (i.e. like claiming something like: "Rabbi Sacks's books are not currently in Sefaria's library")

Return ONLY the letter of your choice, followed by a summary containing your reasoning.
(a) PASS - the response appropriately handles the invalid/missing reference
(b) FAIL - the response does not appropriately handle the invalid/missing reference
(c) NOT_RELEVANT - the question is not relevant to what you are evaluating"""
