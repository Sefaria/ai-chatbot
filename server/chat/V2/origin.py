"""Origin resolution for Braintrust trace tagging."""

DEFAULT_ORIGIN = "dev"
PROD_ORIGINS = frozenset({"sefaria-prod"})


def resolve_origin(caller_origin: str | None) -> str:
    """Resolve the trace origin from caller-provided value and defaults."""
    return (caller_origin or "").strip() or DEFAULT_ORIGIN
