"""Local agent runner — the Django host that runs on a user's own machine.

See docs/plans/2026-09-08-local-agent-runner.md. This package is a settings
profile and entrypoint, not a second web framework: it reuses the chat app's
views so the SSE contract, cancellation and recovery paths are the production
ones by construction.
"""

DEFAULT_PORT = 8899
