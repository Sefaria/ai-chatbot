"""
Router service for flow classification and routing decisions.

The router:
1. Classifies user intent into flows (TRANSLATION, DISCOVERY, DEEP_ENGAGEMENT)
2. Applies guardrails to detect disallowed content
3. Selects appropriate prompts and tools
4. Determines session actions (continue, switch, end)
"""

import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from django.conf import settings
from .guardrails import GuardrailChecker, get_guardrail_checker
from .reason_codes import ReasonCode

logger = logging.getLogger("chat.router")


class Flow(str, Enum):
    """Conversation flow types."""

    TRANSLATION = "TRANSLATION"
    DISCOVERY = "DISCOVERY"
    DEEP_ENGAGEMENT = "DEEP_ENGAGEMENT"
    REFUSE = "REFUSE"


class SessionAction(str, Enum):
    """Session action types."""

    CONTINUE = "CONTINUE"
    SWITCH_FLOW = "SWITCH_FLOW"
    END = "END"


@dataclass
class PromptBundle:
    """Prompt IDs and versions for a turn."""

    core_prompt_id: str = ""
    core_prompt_version: str = ""
    flow_prompt_id: str = ""
    flow_prompt_version: str = ""


@dataclass
class SafetyResult:
    """Safety check result."""

    allowed: bool = True
    refusal_message: str | None = None


@dataclass
class RouteResult:
    """Complete routing decision for a turn."""

    decision_id: str
    flow: Flow
    confidence: float
    reason_codes: list[ReasonCode]
    prompt_bundle: PromptBundle
    tools: list[str]
    session_action: SessionAction
    safety: SafetyResult
    router_latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "decision_id": self.decision_id,
            "flow": self.flow.value,
            "confidence": self.confidence,
            "reason_codes": [code.value for code in self.reason_codes],
            "prompt_bundle": {
                "core_prompt_id": self.prompt_bundle.core_prompt_id,
                "core_prompt_version": self.prompt_bundle.core_prompt_version,
                "flow_prompt_id": self.prompt_bundle.flow_prompt_id,
                "flow_prompt_version": self.prompt_bundle.flow_prompt_version,
            },
            "tools": self.tools,
            "session_action": self.session_action.value,
            "safety": {
                "allowed": self.safety.allowed,
                "refusal_message": self.safety.refusal_message,
            },
            "router_latency_ms": self.router_latency_ms,
        }


# Keyword patterns for flow classification
TRANSLATION_KEYWORDS = [
    r"\btranslate\b",
    r"\btranslation\b",
    r"\btranslate\s+(this|the|it|that)\b",
    r"\binto\s+(english|hebrew|aramaic)\b",
    r"\b(in\s+english|in\s+hebrew|in\s+aramaic)\b",
    r"\bhow\s+do\s+you\s+say\b",
    r"\bwhat\s+does\s+\w+\s+mean\s+in\s+(english|hebrew|aramaic)\b",
    r"\brender\s+(this|the|it|that)\b",
]

DISCOVERY_KEYWORDS = [
    # Source finding
    r"\b(find|search|look\s+for|locate)\s+(sources?|texts?|passages?|quotes?)",
    r"\bwhere\s+(does\s+it\s+say|is\s+it\s+written|can\s+i\s+find)",
    r"\bshow\s+me\s+(the|all)\s+(sources?|texts?|references?)",
    r"\bwhat\s+(does|did)\s+\w+\s+say\s+about",
    r"\bwhere\s+is\s+.*\s+(in\s+tanach|in\s+torah|in\s+the\s+torah)\b",
    # Pattern/counting
    r"\bhow\s+many\s+times\s+(does|is)\b",
    r"\bcount\s+(the|all)\s+(occurrences?|instances?|mentions?)",
    r"\blist\s+(all|the)\s+(references?|sources?|mentions?)",
    # Comparison
    r"\bcompare\s+(the\s+)?(commentaries|translations|versions)",
    r"\bwhat\s+are\s+the\s+different\s+(interpretations|views|opinions)",
    # Specific reference requests
    r"\b(genesis|exodus|leviticus|numbers|deuteronomy)\s+\d+[:\s]\d+",
    r"\b(berakhot|shabbat|eruvin|pesachim|yoma)\s+\d+[ab]?",
    r"\bmishnah?\s+\w+\s+\d+[:\s]\d+",
]

DEEP_ENGAGEMENT_KEYWORDS = [
    # Learning/understanding
    r"\bexplain\s+(to\s+me\s+)?(the|this|what)",
    r"\bwhat\s+(is|are)\s+(the\s+)?(meaning|significance|importance)",
    r"\bhelp\s+me\s+understand",
    r"\bteach\s+me\s+about",
    r"\bwhy\s+(do|did|does|is)\b",
    # Ideas/concepts
    r"\bwhat\s+(does|is)\s+\w+\s+(mean|represent|symbolize)",
    r"\bwhat\s+are\s+(the\s+)?(themes?|ideas?|concepts?)",
    r"\bphilosophy\s+of",
    r"\btheology\s+of",
    # Challenge/discussion
    r"\bchallenge\s+me",
    r"\bdebate\s+(with\s+me|this)",
    r"\bwhat\s+would\s+(you|a)\s+(rabbi|scholar)\s+say",
    r"\bplay\s+devil'?s?\s+advocate",
    r"\bargue\s+(for|against)",
    # Commentary/deep dive cues
    r"\bcommentary\b",
    r"\bcommentaries\b",
    r"\binterpret\b",
    r"\binterpretation\b",
    r"\bgo\s+deep\b",
    r"\bdeep\s+dive\b",
    # Halachic cues map to deep engagement
    r"\b(mutar|assur|מותר|אסור)\b",
    r"\b(halacha|halachah|halakha|הלכה)\b",
    r"\b(psak|פסק|posek|פוסק)\b",
    r"\b(issur|איסור|hetter|היתר)\b",
    r"\b(din|דין|dinim|דינים)\b",
    r"\b(shabbat|shabbos|שבת)\b",
    r"\b(kashrut|kashrus|kosher|כשרות)\b",
    r"\b(tum'ah|tumah|טומאה|taharah|טהרה)\b",
    r"\b(niddah|נידה|mikvah|mikveh|מקווה)\b",
    r"\bis\s+(it|this)\s+(permitted|allowed|forbidden|prohibited)",
    r"\bcan\s+(i|we|one|a\s+jew)\b.*\b(on\s+shabbat|during|while)",
    r"\bwhat\s+is\s+the\s+(halacha|din|law)\b",
    r"\baccording\s+to\s+(halacha|jewish\s+law)",
    r"\bis\s+there\s+a\s+(prohibition|issur)",
]


class RouterService:
    """
    Router service for classifying user messages and making routing decisions.

    Can use either:
    1. AI-based classification (Claude with Braintrust prompts) - Default
    2. Rule-based classification (keyword/pattern matching) - Fallback

    Also includes guardrail checks for safety.
    """

    def __init__(
        self,
        guardrail_checker: GuardrailChecker | None = None,
        use_ai_classifier: bool = True,
        use_ai_guardrails: bool = True,
    ):
        """
        Initialize the router service.

        Args:
            guardrail_checker: Custom guardrail checker (default: built-in)
            use_ai_classifier: Whether to use AI for flow classification (default: True)
            use_ai_guardrails: Whether to use AI for guardrails (default: True)
        """
        # Initialize guardrail checker
        self.use_ai_guardrails = use_ai_guardrails
        self.guardrail_checker = guardrail_checker or get_guardrail_checker(
            use_ai=use_ai_guardrails
        )

        # Initialize flow classifier
        self.use_ai_classifier = use_ai_classifier
        self._ai_router = None

        if use_ai_classifier:
            try:
                from .ai_router import get_ai_flow_router

                # Create AI router with rule-based fallback
                self._ai_router = get_ai_flow_router(
                    fallback_classifier=self._classify_intent_rule_based
                )
                logger.info("Using AI-based flow router with rule-based fallback")
            except Exception as e:
                logger.error(f"Failed to initialize AI flow router: {e}")
                logger.info("Falling back to rule-based flow router")
                self.use_ai_classifier = False

        # Compile rule-based patterns (used for fallback or if AI disabled)
        self._translation_patterns = [re.compile(p, re.IGNORECASE) for p in TRANSLATION_KEYWORDS]
        self._discovery_patterns = [re.compile(p, re.IGNORECASE) for p in DISCOVERY_KEYWORDS]
        self._deep_engagement_patterns = [
            re.compile(p, re.IGNORECASE) for p in DEEP_ENGAGEMENT_KEYWORDS
        ]

    def route(
        self,
        session_id: str,
        user_message: str,
        conversation_summary: str = "",
        previous_flow: str | None = None,
        user_metadata: dict[str, Any] | None = None,
    ) -> RouteResult:
        """
        Route a user message to the appropriate flow.

        Args:
            session_id: Session identifier
            user_message: The user's message
            conversation_summary: Rolling summary of conversation
            previous_flow: The flow from the previous turn (for stickiness)
            user_metadata: Optional user metadata (locale, type, flags)

        Returns:
            RouteResult with complete routing decision
        """
        start_time = time.time()

        # Generate unique decision ID using UUID
        decision_id = f"dec_{uuid.uuid4().hex[:16]}"

        reason_codes: list[ReasonCode] = []

        # Step 1: Apply guardrails
        guardrail_result = self.guardrail_checker.check(user_message)
        reason_codes.extend(guardrail_result.reason_codes)

        if not guardrail_result.allowed:
            # Return REFUSE flow
            return self._create_refuse_result(
                decision_id=decision_id,
                reason_codes=reason_codes,
                refusal_message=guardrail_result.refusal_message or "I can't process this request.",
                start_time=start_time,
            )

        # Step 2: Classify intent (AI or rule-based)
        if self.use_ai_classifier and self._ai_router:
            flow, flow_confidence, flow_reasons = self._ai_router.classify(
                user_message,
                conversation_summary,
                previous_flow,
            )
        else:
            flow, flow_confidence, flow_reasons = self._classify_intent_rule_based(
                user_message,
                conversation_summary,
                previous_flow,
            )
        reason_codes.extend(flow_reasons)

        # Step 3: Determine session action
        session_action = self._determine_session_action(flow, previous_flow)
        if previous_flow and previous_flow != flow.value:
            reason_codes.append(ReasonCode.ROUTE_FLOW_SWITCH_DETECTED)
        elif previous_flow:
            reason_codes.append(ReasonCode.ROUTE_FLOW_STICKINESS)

        # Step 4: Select prompts
        prompt_bundle = self._select_prompts(flow)

        # Step 5: Select tools
        tools = self._select_tools(flow)
        tool_reason = {
            Flow.TRANSLATION: ReasonCode.TOOLS_ADDED_TRANSLATION_SET,
            Flow.DISCOVERY: ReasonCode.TOOLS_ADDED_DISCOVERY_SET,
            Flow.DEEP_ENGAGEMENT: ReasonCode.TOOLS_ADDED_DEEP_ENGAGEMENT_SET,
        }.get(flow)
        if tool_reason:
            reason_codes.append(tool_reason)

        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f"Route decision: flow={flow.value} confidence={flow_confidence:.2f} "
            f"reasons={len(reason_codes)} latency={latency_ms}ms"
        )

        return RouteResult(
            decision_id=decision_id,
            flow=flow,
            confidence=flow_confidence,
            reason_codes=reason_codes,
            prompt_bundle=prompt_bundle,
            tools=tools,
            session_action=session_action,
            safety=SafetyResult(allowed=True),
            router_latency_ms=latency_ms,
        )

    def _classify_intent_rule_based(
        self,
        message: str,
        summary: str,
        previous_flow: str | None,
    ) -> tuple[Flow, float, list[ReasonCode]]:
        """
        Classify the user's intent into a flow using rule-based patterns.

        This is used as fallback when AI classification is disabled or fails.

        Returns (flow, confidence, reason_codes)
        """
        reason_codes = []

        # Count pattern matches for each flow
        translation_score = sum(1 for p in self._translation_patterns if p.search(message))
        discovery_score = sum(1 for p in self._discovery_patterns if p.search(message))
        deep_engagement_score = sum(
            1 for p in self._deep_engagement_patterns if p.search(message)
        )

        # Add weight from previous flow (stickiness)
        if previous_flow == Flow.TRANSLATION.value:
            translation_score += 0.5
        elif previous_flow == Flow.DISCOVERY.value:
            discovery_score += 0.5
        elif previous_flow == Flow.DEEP_ENGAGEMENT.value:
            deep_engagement_score += 0.5

        # Determine winner
        max_score = max(translation_score, discovery_score, deep_engagement_score)

        if max_score == 0:
            # No clear intent, default to deep engagement
            reason_codes.append(ReasonCode.ROUTE_DEFAULT_DEEP_ENGAGEMENT)
            return Flow.DEEP_ENGAGEMENT, 0.5, reason_codes

        # Calculate confidence based on margin
        total_score = translation_score + discovery_score + deep_engagement_score
        confidence = max_score / total_score if total_score > 0 else 0.5

        if translation_score == max_score:
            if translation_score > 0:
                reason_codes.append(ReasonCode.ROUTE_TRANSLATION_KEYWORDS)
            reason_codes.append(ReasonCode.ROUTE_TRANSLATION_INTENT)
            return Flow.TRANSLATION, confidence, reason_codes

        if discovery_score == max_score:
            if discovery_score > 0:
                reason_codes.append(ReasonCode.ROUTE_DISCOVERY_KEYWORDS)
            reason_codes.append(ReasonCode.ROUTE_DISCOVERY_INTENT)
            return Flow.DISCOVERY, confidence, reason_codes

        # Deep engagement wins
        if deep_engagement_score > 0:
            reason_codes.append(ReasonCode.ROUTE_DEEP_ENGAGEMENT_LEARNING)
        reason_codes.append(ReasonCode.ROUTE_DEEP_ENGAGEMENT_INTENT)
        return Flow.DEEP_ENGAGEMENT, confidence, reason_codes

    def _determine_session_action(
        self,
        flow: Flow,
        previous_flow: str | None,
    ) -> SessionAction:
        """Determine the session action based on flow transition."""
        if flow == Flow.REFUSE:
            return SessionAction.END

        if previous_flow is None:
            return SessionAction.CONTINUE

        if previous_flow != flow.value:
            return SessionAction.SWITCH_FLOW

        return SessionAction.CONTINUE

    def _select_prompts(self, flow: Flow) -> PromptBundle:
        """Select prompt IDs based on flow."""
        # These IDs will be looked up in Braintrust
        flow_slug_map = {
            Flow.TRANSLATION: settings.TRANSLATION_PROMPT_SLUG,
            Flow.DISCOVERY: settings.DISCOVERY_PROMPT_SLUG,
            Flow.DEEP_ENGAGEMENT: settings.DEEP_ENGAGEMENT_PROMPT_SLUG,
        }
        flow_slug = flow_slug_map.get(flow) or f"bt_prompt_{flow.value.lower()}"
        return PromptBundle(
            core_prompt_id=settings.CORE_PROMPT_SLUG,
            core_prompt_version="stable",
            flow_prompt_id=flow_slug,
            flow_prompt_version="stable",
        )

    def _select_tools(self, flow: Flow) -> list[str]:
        """Select tools based on flow."""
        # Tool names that will be filtered from the full tool list
        if flow == Flow.TRANSLATION:
            return [
                "get_text",
                "search_in_dictionaries",
            ]

        if flow == Flow.DISCOVERY:
            return [
                "get_text",
                "text_search",
                "get_current_calendar",
                "english_semantic_search",
                "search_in_book",
                "search_in_dictionaries",
                "get_links_between_texts",
                "get_english_translations",
                "get_topic_details",
                "get_text_or_category_shape",
                "get_text_catalogue_info",
                "get_available_manuscripts",
                "get_manuscript_image",
                "clarify_name_argument",
                "clarify_search_path_filter",
            ]

        if flow == Flow.DEEP_ENGAGEMENT:
            return [
                "get_text",
                "text_search",
                "get_current_calendar",
                "english_semantic_search",
                "search_in_book",
                "search_in_dictionaries",
                "get_links_between_texts",
                "get_english_translations",
                "get_topic_details",
                "get_text_or_category_shape",
                "get_text_catalogue_info",
                "get_available_manuscripts",
                "get_manuscript_image",
                "clarify_name_argument",
                "clarify_search_path_filter",
            ]

        # REFUSE flow - no tools
        return []

    def _create_refuse_result(
        self,
        decision_id: str,
        reason_codes: list[ReasonCode],
        refusal_message: str,
        start_time: float,
    ) -> RouteResult:
        """Create a REFUSE flow result."""
        reason_codes.append(ReasonCode.TOOLS_NONE_ATTACHED)
        latency_ms = int((time.time() - start_time) * 1000)

        return RouteResult(
            decision_id=decision_id,
            flow=Flow.REFUSE,
            confidence=1.0,
            reason_codes=reason_codes,
            prompt_bundle=PromptBundle(),
            tools=[],
            session_action=SessionAction.END,
            safety=SafetyResult(allowed=False, refusal_message=refusal_message),
            router_latency_ms=latency_ms,
        )


# Default router service instance
_default_router = None


def get_router_service(
    use_ai_classifier: bool | None = None,
    use_ai_guardrails: bool | None = None,
) -> RouterService:
    """
    Get or create the default router service.

    Args:
        use_ai_classifier: Override to enable/disable AI classification.
                          If None, reads from ROUTER_USE_AI env var (default: True)
        use_ai_guardrails: Override to enable/disable AI guardrails.
                          If None, reads from GUARDRAILS_USE_AI env var (default: True)

    Returns:
        RouterService instance
    """
    global _default_router

    # Read from environment if not specified
    if use_ai_classifier is None:
        use_ai_classifier = os.environ.get("ROUTER_USE_AI", "true").lower() == "true"
    if use_ai_guardrails is None:
        use_ai_guardrails = os.environ.get("GUARDRAILS_USE_AI", "true").lower() == "true"

    # Create new instance if settings changed or doesn't exist
    if _default_router is None:
        _default_router = RouterService(
            use_ai_classifier=use_ai_classifier,
            use_ai_guardrails=use_ai_guardrails,
        )

    return _default_router
