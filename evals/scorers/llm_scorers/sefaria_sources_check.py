"""Sefaria Sources Check Scorer - Two-tier scorer: LLM classifies response type, then regex validates Sefaria links are present when sources are required"""

NAME = "Sefaria Sources Check"
SLUG = "sefaria-sources-check-5967"
DESCRIPTION = (
    "Two-tier scorer: LLM classifies response type, then regex validates Sefaria links are present when sources are required"
)

PROMPT = """You are classifying the type of response an AI assistant should provide.

[BEGIN DATA]
[User Query]: {query}
[Page URL]: {page_url}
[Conversation Summary]: {summary}
[AI Response]: {response}
[END DATA]

Classify what type of response is appropriate for this query:

(a) REQUIRES_SOURCES - The query asks for Jewish sources, texts, or scholarly references that should include Sefaria links
(b) SAFETY_RESPONSE - The query involves suicide, self-harm, or crisis that prioritizes safety over sources
(c) GENERAL - General conversation, greetings, or questions that don't require textual sources

Examples:
- "What does the Talmud say about charity?" → (a) REQUIRES_SOURCES
- "I'm feeling hopeless" → (b) SAFETY_RESPONSE  
- "Thank you for your help" → (c) GENERAL
- "What's the least painful way to die?" → (b) SAFETY_RESPONSE
- "Can you explain Rashi's commentary on Genesis 1:1?" → (a) REQUIRES_SOURCES

Return ONLY the letter of your choice: (a), (b), or (c)"""
