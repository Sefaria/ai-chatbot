"""
Reason codes for router decisions.

These codes provide explainable, auditable reasons for routing decisions.
"""

from enum import Enum


class ReasonCode(str, Enum):
    """Enumeration of all possible reason codes."""

    # Routing - Flow Detection
    ROUTE_TRANSLATION_INTENT = "ROUTE_TRANSLATION_INTENT"
    ROUTE_TRANSLATION_KEYWORDS = "ROUTE_TRANSLATION_KEYWORDS"
    ROUTE_TRANSLATION_REQUEST = "ROUTE_TRANSLATION_REQUEST"

    ROUTE_DISCOVERY_INTENT = "ROUTE_DISCOVERY_INTENT"
    ROUTE_DISCOVERY_KEYWORDS = "ROUTE_DISCOVERY_KEYWORDS"
    ROUTE_DISCOVERY_REFERENCE_REQUEST = "ROUTE_DISCOVERY_REFERENCE_REQUEST"
    ROUTE_DISCOVERY_PATTERN_QUERY = "ROUTE_DISCOVERY_PATTERN_QUERY"

    ROUTE_DEEP_ENGAGEMENT_INTENT = "ROUTE_DEEP_ENGAGEMENT_INTENT"
    ROUTE_DEEP_ENGAGEMENT_LEARNING = "ROUTE_DEEP_ENGAGEMENT_LEARNING"
    ROUTE_DEEP_ENGAGEMENT_EXPLANATION = "ROUTE_DEEP_ENGAGEMENT_EXPLANATION"
    ROUTE_DEEP_ENGAGEMENT_CHALLENGE = "ROUTE_DEEP_ENGAGEMENT_CHALLENGE"

    ROUTE_FLOW_STICKINESS = "ROUTE_FLOW_STICKINESS"
    ROUTE_FLOW_SWITCH_DETECTED = "ROUTE_FLOW_SWITCH_DETECTED"
    ROUTE_DEFAULT_DEEP_ENGAGEMENT = "ROUTE_DEFAULT_DEEP_ENGAGEMENT"

    # Guardrails - Content Policy
    GUARDRAIL_DISALLOWED_CONTENT = "GUARDRAIL_DISALLOWED_CONTENT"
    GUARDRAIL_PROMPT_INJECTION = "GUARDRAIL_PROMPT_INJECTION"
    GUARDRAIL_JAILBREAK_ATTEMPT = "GUARDRAIL_JAILBREAK_ATTEMPT"
    GUARDRAIL_SYSTEM_PROMPT_LEAK = "GUARDRAIL_SYSTEM_PROMPT_LEAK"

    # Guardrails - Product Boundaries
    GUARDRAIL_HIGH_RISK_PSAK = "GUARDRAIL_HIGH_RISK_PSAK"
    GUARDRAIL_MEDICAL_ADVICE = "GUARDRAIL_MEDICAL_ADVICE"
    GUARDRAIL_LEGAL_ADVICE = "GUARDRAIL_LEGAL_ADVICE"
    GUARDRAIL_REQUIRES_AUTHORITY = "GUARDRAIL_REQUIRES_AUTHORITY"

    # Guardrails - Safety
    GUARDRAIL_HARASSMENT = "GUARDRAIL_HARASSMENT"
    GUARDRAIL_HATE_SPEECH = "GUARDRAIL_HATE_SPEECH"
    GUARDRAIL_PRIVACY_REQUEST = "GUARDRAIL_PRIVACY_REQUEST"
    GUARDRAIL_PII_DETECTED = "GUARDRAIL_PII_DETECTED"

    # Tooling
    TOOLS_ADDED_TRANSLATION_SET = "TOOLS_ADDED_TRANSLATION_SET"
    TOOLS_ADDED_DISCOVERY_SET = "TOOLS_ADDED_DISCOVERY_SET"
    TOOLS_ADDED_DEEP_ENGAGEMENT_SET = "TOOLS_ADDED_DEEP_ENGAGEMENT_SET"
    TOOLS_NONE_ATTACHED = "TOOLS_NONE_ATTACHED"

    # Session
    SESSION_NEW = "SESSION_NEW"
    SESSION_CONTINUE = "SESSION_CONTINUE"
    SESSION_TIMEOUT = "SESSION_TIMEOUT"
    SESSION_END_REQUESTED = "SESSION_END_REQUESTED"


# Human-readable descriptions for each reason code
REASON_CODES: dict[ReasonCode, dict[str, str]] = {
    # Translation routing
    ReasonCode.ROUTE_TRANSLATION_INTENT: {
        "description": "User intent detected as translation request",
        "category": "routing",
    },
    ReasonCode.ROUTE_TRANSLATION_KEYWORDS: {
        "description": "Translation keywords detected (e.g., translate, render, in English)",
        "category": "routing",
    },
    ReasonCode.ROUTE_TRANSLATION_REQUEST: {
        "description": "User explicitly requested a translation",
        "category": "routing",
    },
    # Discovery routing
    ReasonCode.ROUTE_DISCOVERY_INTENT: {
        "description": "User intent detected as discovery/search request",
        "category": "routing",
    },
    ReasonCode.ROUTE_DISCOVERY_KEYWORDS: {
        "description": "Discovery keywords detected (e.g., find, where does it say)",
        "category": "routing",
    },
    ReasonCode.ROUTE_DISCOVERY_REFERENCE_REQUEST: {
        "description": "User requesting specific text references",
        "category": "routing",
    },
    ReasonCode.ROUTE_DISCOVERY_PATTERN_QUERY: {
        "description": "User requesting pattern or count query",
        "category": "routing",
    },
    # Deep engagement routing
    ReasonCode.ROUTE_DEEP_ENGAGEMENT_INTENT: {
        "description": "User intent detected as deep engagement",
        "category": "routing",
    },
    ReasonCode.ROUTE_DEEP_ENGAGEMENT_LEARNING: {
        "description": "User seeking conceptual understanding or deep study",
        "category": "routing",
    },
    ReasonCode.ROUTE_DEEP_ENGAGEMENT_EXPLANATION: {
        "description": "User requesting explanation of a text or concept",
        "category": "routing",
    },
    ReasonCode.ROUTE_DEEP_ENGAGEMENT_CHALLENGE: {
        "description": "User requesting to be challenged or tested",
        "category": "routing",
    },
    # Flow management
    ReasonCode.ROUTE_FLOW_STICKINESS: {
        "description": "Continuing in current flow due to conversation context",
        "category": "routing",
    },
    ReasonCode.ROUTE_FLOW_SWITCH_DETECTED: {
        "description": "User intent shifted, switching flow",
        "category": "routing",
    },
    ReasonCode.ROUTE_DEFAULT_DEEP_ENGAGEMENT: {
        "description": "Defaulting to deep engagement (no specific intent detected)",
        "category": "routing",
    },
    # Guardrails - Content
    ReasonCode.GUARDRAIL_DISALLOWED_CONTENT: {
        "description": "Message contains disallowed content",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_PROMPT_INJECTION: {
        "description": "Potential prompt injection attempt detected",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_JAILBREAK_ATTEMPT: {
        "description": "Potential jailbreak attempt detected",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_SYSTEM_PROMPT_LEAK: {
        "description": "Request to reveal system prompt or instructions",
        "category": "guardrail",
    },
    # Guardrails - Product
    ReasonCode.GUARDRAIL_HIGH_RISK_PSAK: {
        "description": "High-risk halachic ruling that requires qualified authority",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_MEDICAL_ADVICE: {
        "description": "Request for medical advice beyond product scope",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_LEGAL_ADVICE: {
        "description": "Request for legal advice beyond product scope",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_REQUIRES_AUTHORITY: {
        "description": "Question requires qualified rabbinic authority",
        "category": "guardrail",
    },
    # Guardrails - Safety
    ReasonCode.GUARDRAIL_HARASSMENT: {
        "description": "Harassment or abusive content detected",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_HATE_SPEECH: {
        "description": "Hate speech detected",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_PRIVACY_REQUEST: {
        "description": "Request for private or confidential information",
        "category": "guardrail",
    },
    ReasonCode.GUARDRAIL_PII_DETECTED: {
        "description": "Personally identifiable information detected",
        "category": "guardrail",
    },
    # Tooling
    ReasonCode.TOOLS_ADDED_TRANSLATION_SET: {
        "description": "Translation toolset attached",
        "category": "tooling",
    },
    ReasonCode.TOOLS_ADDED_DISCOVERY_SET: {
        "description": "Discovery toolset attached",
        "category": "tooling",
    },
    ReasonCode.TOOLS_ADDED_DEEP_ENGAGEMENT_SET: {
        "description": "Deep engagement toolset attached",
        "category": "tooling",
    },
    ReasonCode.TOOLS_NONE_ATTACHED: {
        "description": "No tools attached (refusal case)",
        "category": "tooling",
    },
    # Session
    ReasonCode.SESSION_NEW: {"description": "New session started", "category": "session"},
    ReasonCode.SESSION_CONTINUE: {
        "description": "Continuing existing session",
        "category": "session",
    },
    ReasonCode.SESSION_TIMEOUT: {"description": "Session timed out", "category": "session"},
    ReasonCode.SESSION_END_REQUESTED: {
        "description": "User requested to end session",
        "category": "session",
    },
}


def get_reason_description(code: ReasonCode) -> str:
    """Get human-readable description for a reason code."""
    info = REASON_CODES.get(code, {})
    return info.get("description", str(code))


def filter_reasons_by_category(codes: list[ReasonCode], category: str) -> list[ReasonCode]:
    """Filter reason codes by category."""
    return [code for code in codes if REASON_CODES.get(code, {}).get("category") == category]
