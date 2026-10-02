"""Persona resolution for persona-aware hosts (Library Next)."""

PERSONAS = ("newcomer", "learner", "educator", "scholar")


def resolve_persona(value: object) -> str | None:
    """Return the persona if it is a known value, otherwise None.

    Unknown or blank values are ignored rather than rejected so a misconfigured
    host still gets a working (persona-less) assistant.
    """
    if not isinstance(value, str):
        return None
    persona = value.strip().lower()
    return persona if persona in PERSONAS else None
