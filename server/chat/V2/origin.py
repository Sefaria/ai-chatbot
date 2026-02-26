"""Origin resolution for Braintrust trace tagging."""

DEFAULT_ORIGIN = "dev"
PROD_ORIGINS = ["sefaria-prod"]


def resolve_origin(caller_origin: str | None) -> tuple[str, bool]:
    """Resolve the trace origin from caller-provided value and defaults.

    Returns (origin_string, is_production).
    """
    origin = (caller_origin or "").strip() or DEFAULT_ORIGIN
    is_prod = origin in PROD_ORIGINS
    return origin, is_prod
