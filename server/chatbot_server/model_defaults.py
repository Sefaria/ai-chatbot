"""Default model identifiers and sampling params shared by settings.py, the
runtime callsites, and prompts/*.py.

Pure Python — no Django, no other imports — so prompt push scripts can import
this without booting Django.
"""

AGENT_MODEL = "claude-sonnet-4-6"
AGENT_MAX_TOKENS = 8000
AGENT_TEMPERATURE = 0.7

GUARDRAIL_MODEL = "claude-haiku-4-5-20251001"
GUARDRAIL_MAX_TOKENS = 256
GUARDRAIL_TEMPERATURE = 0.0

ROUTER_MODEL = "claude-haiku-4-5-20251001"
ROUTER_MAX_TOKENS = 256
ROUTER_TEMPERATURE = 0.0
