"""Origin resolution for Braintrust trace tagging."""

DEFAULT_ORIGIN = "dev"
# Must match the origin string sent by callers (e.g. Sefaria frontend sends "sefaria-production").
# Prod origins are excluded from the "dev" Braintrust tag (see trace_logger.py).
PROD_ORIGINS = frozenset({"sefaria-production"})


def resolve_origin(caller_origin: str | None) -> str:
    """Resolve the trace origin from caller-provided value and defaults."""
    return (caller_origin or "").strip() or DEFAULT_ORIGIN
