"""
Reason codes for router decisions.

These codes provide explainable, auditable reasons for routing decisions.
"""

from enum import Enum


class ReasonCode(str, Enum):
    """Enumeration of all possible reason codes."""

    # Routing - Flow Detection
    ROUTE_HALACHIC_INTENT = "ROUTE_HALACHIC_INTENT"
    ROUTE_HALACHIC_KEYWORDS = "ROUTE_HALACHIC_KEYWORDS"
    ROUTE_HALACHIC_QUESTION_PATTERN = "ROUTE_HALACHIC_QUESTION_PATTERN"

    ROUTE_SEARCH_INTENT = "ROUTE_SEARCH_INTENT"
    ROUTE_SEARCH_KEYWORDS = "ROUTE_SEARCH_KEYWORDS"
    ROUTE_SEARCH_REFERENCE_REQUEST = "ROUTE_SEARCH_REFERENCE_REQUEST"
    ROUTE_SEARCH_PATTERN_QUERY = "ROUTE_SEARCH_PATTERN_QUERY"

    ROUTE_GENERAL_INTENT = "ROUTE_GENERAL_INTENT"
    ROUTE_GENERAL_LEARNING = "ROUTE_GENERAL_LEARNING"
    ROUTE_GENERAL_EXPLANATION = "ROUTE_GENERAL_EXPLANATION"
    ROUTE_GENERAL_CHALLENGE = "ROUTE_GENERAL_CHALLENGE"

    ROUTE_FLOW_STICKINESS = "ROUTE_FLOW_STICKINESS"
    ROUTE_FLOW_SWITCH_DETECTED = "ROUTE_FLOW_SWITCH_DETECTED"
    ROUTE_DEFAULT_GENERAL = "ROUTE_DEFAULT_GENERAL"

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
    TOOLS_ADDED_HALACHIC_SET = "TOOLS_ADDED_HALACHIC_SET"
    TOOLS_ADDED_SEARCH_SET = "TOOLS_ADDED_SEARCH_SET"
    TOOLS_MINIMAL_GENERAL_SET = "TOOLS_MINIMAL_GENERAL_SET"
    TOOLS_NONE_ATTACHED = "TOOLS_NONE_ATTACHED"

    # Session
    SESSION_NEW = "SESSION_NEW"
    SESSION_CONTINUE = "SESSION_CONTINUE"
    SESSION_TIMEOUT = "SESSION_TIMEOUT"
    SESSION_END_REQUESTED = "SESSION_END_REQUESTED"


# Human-readable descriptions for each reason code
REASON_CODES: dict[ReasonCode, dict[str, str]] = {
    # Halachic routing
    ReasonCode.ROUTE_HALACHIC_INTENT: {
        "description": "User intent detected as halachic inquiry",
        "category": "routing",
    },
    ReasonCode.ROUTE_HALACHIC_KEYWORDS: {
        "description": "Halachic keywords detected (e.g., mutar, assur, din)",
        "category": "routing",
    },
    ReasonCode.ROUTE_HALACHIC_QUESTION_PATTERN: {
        "description": "Question pattern matches halachic inquiry (e.g., 'Is it permitted...')",
        "category": "routing",
    },
    # Search routing
    ReasonCode.ROUTE_SEARCH_INTENT: {
        "description": "User intent detected as source search",
        "category": "routing",
    },
    ReasonCode.ROUTE_SEARCH_KEYWORDS: {
        "description": "Search keywords detected (e.g., 'find', 'where does it say')",
        "category": "routing",
    },
    ReasonCode.ROUTE_SEARCH_REFERENCE_REQUEST: {
        "description": "User requesting specific text references",
        "category": "routing",
    },
    ReasonCode.ROUTE_SEARCH_PATTERN_QUERY: {
        "description": "User requesting pattern or count query",
        "category": "routing",
    },
    # General routing
    ReasonCode.ROUTE_GENERAL_INTENT: {
        "description": "User intent detected as general learning",
        "category": "routing",
    },
    ReasonCode.ROUTE_GENERAL_LEARNING: {
        "description": "User seeking conceptual understanding or ideas",
        "category": "routing",
    },
    ReasonCode.ROUTE_GENERAL_EXPLANATION: {
        "description": "User requesting explanation of concept or text",
        "category": "routing",
    },
    ReasonCode.ROUTE_GENERAL_CHALLENGE: {
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
    ReasonCode.ROUTE_DEFAULT_GENERAL: {
        "description": "Defaulting to general flow (no specific intent detected)",
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
    ReasonCode.TOOLS_ADDED_HALACHIC_SET: {
        "description": "Halachic toolset attached",
        "category": "tooling",
    },
    ReasonCode.TOOLS_ADDED_SEARCH_SET: {
        "description": "Search toolset attached",
        "category": "tooling",
    },
    ReasonCode.TOOLS_MINIMAL_GENERAL_SET: {
        "description": "Minimal general toolset attached",
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
