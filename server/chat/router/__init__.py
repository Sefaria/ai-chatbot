"""
Router module for flow classification and guardrails.

The router decides:
- Which flow to use (HALACHIC, GENERAL, SEARCH, REFUSE)
- Which prompts and tools to attach
- Whether to continue, switch flow, or end the conversation

Supports both AI-based (via Braintrust prompts) and rule-based classification.
"""

from .router_service import RouterService, RouteResult, Flow, SessionAction, get_router_service
from .reason_codes import ReasonCode, REASON_CODES
from .guardrails import GuardrailChecker, GuardrailResult, get_guardrail_checker
from .braintrust_client import BraintrustPromptClient, get_braintrust_client

# Optional: AI-based components (may not be available if dependencies missing)
try:
    from .ai_guardrails import AIGuardrailChecker, get_ai_guardrail_checker
    from .ai_router import AIFlowRouter, get_ai_flow_router
    _ai_available = True
except ImportError:
    _ai_available = False
    AIGuardrailChecker = None
    get_ai_guardrail_checker = None
    AIFlowRouter = None
    get_ai_flow_router = None

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
    'get_guardrail_checker',
    'BraintrustPromptClient',
    'get_braintrust_client',
]

# Add AI components to exports if available
if _ai_available:
    __all__.extend([
        'AIGuardrailChecker',
        'get_ai_guardrail_checker',
        'AIFlowRouter',
        'get_ai_flow_router',
    ])

