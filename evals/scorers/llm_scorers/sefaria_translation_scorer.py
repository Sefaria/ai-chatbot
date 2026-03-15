"""Translations_Scorer Scorer - Translation Scorer. Skips scoring for non-relevant queries."""

NAME = "Translations_Scorer"
SLUG = "sefaria-translation-scorer"
DESCRIPTION = (
    "Translation Scorer. Skips scoring for non-relevant queries."
)

PROMPT = """First, determine if this query is a request to translate a Jewish text, define a Hebrew/Aramaic phrase from a Jewish text, or ask "what does [text] mean?".

If the query is NOT a request for translation or definition of a Jewish text, return ONLY:
(c) NOT_RELEVANT

If the query IS a translation/definition request, evaluate the response based on the following criteria.

You are evaluating whether an AI assistant for a Jewish library appropriately handles translation requests for texts not yet translated in Sefaria's database.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

Evaluate whether the response appropriately prioritizes checking for existing translations before generating new ones, and provides accurate, transparent translations when needed.

An answer PASSES if:
- It first checks Sefaria's database for existing published translations before generating a new one.
- It clearly states when a translation is AI-generated and not from a published source.
- It provides functional translations following the plain meaning (peshat) of the text rather than imposing midrashic interpretations.
- It includes helpful contextual notes or key terminology when this aids comprehension.
- It offers to provide additional explanation without overwhelming the user with unsolicited commentary.
- It answered in the desired language.

An answer FAILS if:
- It generates a translation without first checking whether published translations exist in Sefaria.
- It presents an AI-generated translation as authoritative or implies it's from a published source.
- It provides overly interpretive translations that stray from the plain meaning of the text.
- It omits acknowledgment that the translation is AI-generated when no published version exists.
- It provides excessive explanation when the user only requested a translation.

Here are examples of successful responses to queries:

EXAMPLE 1: Text with existing English translations
Query: "Translate Genesis 1:1 for me"
Good response: Present multiple published translations from Sefaria (JPS, Koren, etc.). Explain briefly that different translations reflect different scholarly approaches. MUST NOT generate AI translation when published versions exist.

EXAMPLE 2: Common phrase with existing translations
Query: "What does הַנִּצָּנִים נִרְאוּ בָאָרֶץ mean?"
Good response: Identify the source (Song of Songs 2:12). Provide published translations from Sefaria. Give brief context. MUST NOT generate AI translation.

EXAMPLE 3: Untranslated kabbalistic text
Query: "Translate: ואחר כך יצא הקו השמאלי..."
Good response: State that no published translation exists in Sefaria. Provide functional AI-generated translation. Clearly label it as "Translation (AI-generated)". Include key terminology explanations.

EXAMPLE 4: Ambiguous "what does this mean" query
Query: "What does 'ואהבת לרעך כמוך' mean?"
Good response: Recognize this is Leviticus 19:18. Provide published translation from Sefaria. Note this is a foundational ethical principle. MUST NOT generate AI translation.

EXAMPLE 5: Request to translate into non-English language (Spanish)
Query: "Can you translate Genesis 1:1 into Spanish?"
Good response (If exists): "Genesis 1:1 has a Spanish translation in Sefaria..." Provide the official version.
Good response (If missing): "I don't see a published Spanish translation... Here's an AI-generated translation... Important note: This is an AI-generated translation."

EXAMPLE 6: Partial or unclear text
Query: "Translate this: בראשית ברא..."
Good response: Recognize as beginning of Genesis 1:1. Provide translation of the complete verse from Sefaria. Ask if user wants just these words or full verse.

EXAMPLE 7: Text with no existing translation in requested language
Query: "Please translate Mishnah Berakhot 1:1 into French"
Good response: "I don't see a published French translation... Here's an AI-generated translation... Important note: This is an AI-generated translation."

Return ONLY the letter of your choice, followed by a summary containing your reasoning.
(a) PASS - the response is appropriate for these criteria
(b) FAIL - the response is not appropriate for these criteria"""
