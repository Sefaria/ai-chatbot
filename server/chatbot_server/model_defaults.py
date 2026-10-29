"""Default model identifiers and sampling params shared by settings.py, the
runtime callsites, and prompts/*.py.

Pure Python — no Django, no other imports — so prompt push scripts can import
this without booting Django.
"""

AGENT_MODEL = "claude-sonnet-5"
AGENT_MAX_TOKENS = 8000
AGENT_TEMPERATURE = 0.7

# The appetizer forces tool_choice, which Sonnet 5.5 returns a 400 on - keep
# this pinned independent of AGENT_MODEL until that's supported.
APPETIZER_MODEL = "claude-sonnet-4-6"

GUARDRAIL_MODEL = "claude-haiku-4-5-20251001"
GUARDRAIL_MAX_TOKENS = 256
GUARDRAIL_TEMPERATURE = 0.0

ROUTER_MODEL = "claude-haiku-4-5-20251001"
ROUTER_MAX_TOKENS = 256
ROUTER_TEMPERATURE = 0.0
