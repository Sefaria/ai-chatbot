"""
Personal memory: the widget's "Personalize Responses" answers.

The widget keeps them in the browser and sends them with every message
(ChatRequestSerializer.memory). They are rendered into a prompt section for that turn
(prompt_fragments.USER_MEMORY_SECTION); nothing is stored server-side.
"""

from .prompts.prompt_fragments import USER_MEMORY_SECTION

# Option keys the widget sends → how the agent reads them.
EXPERIENCE = {
    "never": "has never learned Jewish texts before",
    "a_bit": "has learned Jewish texts a bit",
    "grew_up": "grew up learning Jewish texts",
    "recent": "recently became interested in Jewish texts",
}
ORIENTATION = {
    "spiritual": "more spiritual than intellectual",
    "intellectual": "more intellectual than spiritual",
    "both": "both spiritual and intellectual",
    "unsure": "not sure whether spiritual or intellectual",
}
HEBREW = {
    "none": "no Hebrew",
    "alphabet": "knows the Hebrew alphabet",
    "some": "knows some Hebrew",
    "strong": "strong Hebrew",
    "fluent": "fluent Hebrew",
}


def _describe(value: str, options: dict[str, str]) -> str:
    return options.get(value) or f'"{value}" (in their words)'


def build_memory_prompt_text(memory: dict | None) -> str | None:
    """Render the answers as prompt lines, or None when there is nothing to say."""
    memory = memory or {}
    lines = []
    if memory.get("experience"):
        lines.append(f"- Background: {_describe(memory['experience'], EXPERIENCE)}")
    if memory.get("orientation"):
        lines.append(f"- Orientation: {_describe(memory['orientation'], ORIENTATION)}")
    if memory.get("hebrew"):
        lines.append(f"- Hebrew: {_describe(memory['hebrew'], HEBREW)}")
    if memory.get("notes"):
        lines.append(f'- In their own words: "{memory["notes"]}"')
    return USER_MEMORY_SECTION.format(memory_lines="\n".join(lines)) if lines else None
