"""
Router module for flow classification and guardrails.

The router decides:
- Which flow to use (HALACHIC, GENERAL, SEARCH, REFUSE)
- Which prompts and tools to attach
- Whether to continue, switch flow, or end the conversation
"""

from .router_service import RouterService, RouteResult, Flow, SessionAction, get_router_service
from .reason_codes import ReasonCode, REASON_CODES
from .guardrails import GuardrailChecker, GuardrailResult

__all__ = [
    'RouterService',
    'RouteResult',
    'Flow',
    'SessionAction',
    'get_router_service',
    'ReasonCode',
    'REASON_CODES',
    'GuardrailChecker',
    'GuardrailResult',
]

