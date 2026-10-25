"""Standalone public MCP server built on the chatbot's Sefaria tool layer.

The agent (``chat.V2.agent``) and this server share one implementation of every
tool. Which tools reach the public surface is declared by the ``surfaces`` marker
on each schema in ``chat.V2.agent.tool_schemas``.
"""

__all__ = ["build_app", "build_server"]


def __getattr__(name: str):
    if name in __all__:
        from . import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
