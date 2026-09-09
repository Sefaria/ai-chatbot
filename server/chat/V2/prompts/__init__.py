"""
Prompt management module with Braintrust integration.

Provides:
- Core prompt retrieval from Braintrust
- Prompt versioning and caching

Imports are resolved lazily via ``__getattr__`` so that importing
``prompt_fragments`` (plain strings, no dependencies) does not pull in
``prompt_service``, which needs Django settings and Braintrust. The agent
package depends on that split.
"""

from typing import TYPE_CHECKING, Any

_EXPORTS = {
    "CorePrompt": ".prompt_service",
    "PromptService": ".prompt_service",
    "get_prompt_service": ".prompt_service",
}

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .prompt_service import CorePrompt, PromptService, get_prompt_service


def __getattr__(name: str) -> Any:
    """Import an export on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(_EXPORTS)


__all__ = [
    "CorePrompt",
    "PromptService",
    "get_prompt_service",
]

assert sorted(__all__) == sorted(_EXPORTS), "__all__ and _EXPORTS have drifted"
