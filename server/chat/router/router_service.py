"""
Router service for flow classification and routing decisions.

The router:
1. Classifies user intent into flows (HALACHIC, GENERAL, SEARCH)
2. Applies guardrails to detect disallowed content
3. Selects appropriate prompts and tools
4. Determines session actions (continue, switch, end)
"""

import os
import re
import time
import logging
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

from .reason_codes import ReasonCode
from .guardrails import GuardrailChecker, GuardrailResult, get_guardrail_checker

logger = logging.getLogger('chat.router')


class Flow(str, Enum):
    """Conversation flow types."""
    HALACHIC = "HALACHIC"
    GENERAL = "GENERAL"
    SEARCH = "SEARCH"
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
    refusal_message: Optional[str] = None


@dataclass
class RouteResult:
    """Complete routing decision for a turn."""
    decision_id: str
    flow: Flow
    confidence: float
    reason_codes: List[ReasonCode]
    prompt_bundle: PromptBundle
    tools: List[str]
    session_action: SessionAction
    safety: SafetyResult
    router_latency_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
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
HALACHIC_KEYWORDS = [
    # Hebrew/Aramaic terms
    r"\b(mutar|assur|מותר|אסור)\b",
    r"\b(halacha|halachah|halakha|הלכה)\b",
    r"\b(psak|פסק|posek|פוסק)\b",
    r"\b(issur|איסור|hetter|היתר)\b",
    r"\b(din|דין|dinim|דינים)\b",
    r"\b(shabbat|shabbos|שבת)\b",
    r"\b(kashrut|kashrus|kosher|כשרות)\b",
    r"\b(tum'ah|tumah|טומאה|taharah|טהרה)\b",
    r"\b(niddah|נידה|mikvah|mikveh|מקווה)\b",
    
    # Question patterns
    r"\bis\s+(it|this)\s+(permitted|allowed|forbidden|prohibited)",
    r"\bcan\s+(i|we|one|a\s+jew)\b.*\b(on\s+shabbat|during|while)",
    r"\bwhat\s+is\s+the\s+(halacha|din|law)\b",
    r"\baccording\s+to\s+(halacha|jewish\s+law)",
    r"\bis\s+there\s+a\s+(prohibition|issur)",
]

SEARCH_KEYWORDS = [
    # Source finding
    r"\b(find|search|look\s+for|locate)\s+(sources?|texts?|passages?|quotes?)",
    r"\bwhere\s+(does\s+it\s+say|is\s+it\s+written|can\s+i\s+find)",
    r"\bshow\s+me\s+(the|all)\s+(sources?|texts?|references?)",
    r"\bwhat\s+(does|did)\s+\w+\s+say\s+about",
    
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

GENERAL_KEYWORDS = [
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
]


class RouterService:
    """
    Router service for classifying user messages and making routing decisions.
    
    Uses a combination of:
    1. Rule-based keyword/pattern matching
    2. Guardrail checks
    3. Conversation context (previous flow, summary)
    """
    
    def __init__(
        self,
        guardrail_checker: Optional[GuardrailChecker] = None,
        use_llm_classifier: bool = False,
    ):
        """
        Initialize the router service.
        
        Args:
            guardrail_checker: Custom guardrail checker (default: built-in)
            use_llm_classifier: Whether to use LLM for ambiguous classification
        """
        self.guardrail_checker = guardrail_checker or get_guardrail_checker()
        self.use_llm_classifier = use_llm_classifier
        
        # Compile patterns
        self._halachic_patterns = [re.compile(p, re.IGNORECASE) for p in HALACHIC_KEYWORDS]
        self._search_patterns = [re.compile(p, re.IGNORECASE) for p in SEARCH_KEYWORDS]
        self._general_patterns = [re.compile(p, re.IGNORECASE) for p in GENERAL_KEYWORDS]
    
    def route(
        self,
        session_id: str,
        user_message: str,
        conversation_summary: str = "",
        previous_flow: Optional[str] = None,
        user_metadata: Optional[Dict[str, Any]] = None,
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
        
        reason_codes: List[ReasonCode] = []
        
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
        
        # Step 2: Classify intent
        flow, flow_confidence, flow_reasons = self._classify_intent(
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
            Flow.HALACHIC: ReasonCode.TOOLS_ADDED_HALACHIC_SET,
            Flow.SEARCH: ReasonCode.TOOLS_ADDED_SEARCH_SET,
            Flow.GENERAL: ReasonCode.TOOLS_MINIMAL_GENERAL_SET,
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
    
    def _classify_intent(
        self,
        message: str,
        summary: str,
        previous_flow: Optional[str],
    ) -> tuple[Flow, float, List[ReasonCode]]:
        """
        Classify the user's intent into a flow.
        
        Returns (flow, confidence, reason_codes)
        """
        reason_codes = []
        
        # Count pattern matches for each flow
        halachic_score = sum(1 for p in self._halachic_patterns if p.search(message))
        search_score = sum(1 for p in self._search_patterns if p.search(message))
        general_score = sum(1 for p in self._general_patterns if p.search(message))
        
        # Add weight from previous flow (stickiness)
        if previous_flow == Flow.HALACHIC.value:
            halachic_score += 0.5
        elif previous_flow == Flow.SEARCH.value:
            search_score += 0.5
        elif previous_flow == Flow.GENERAL.value:
            general_score += 0.5
        
        # Determine winner
        max_score = max(halachic_score, search_score, general_score)
        
        if max_score == 0:
            # No clear intent, default to general
            reason_codes.append(ReasonCode.ROUTE_DEFAULT_GENERAL)
            return Flow.GENERAL, 0.5, reason_codes
        
        # Calculate confidence based on margin
        total_score = halachic_score + search_score + general_score
        confidence = max_score / total_score if total_score > 0 else 0.5
        
        if halachic_score == max_score:
            if halachic_score > 0:
                reason_codes.append(ReasonCode.ROUTE_HALACHIC_KEYWORDS)
            reason_codes.append(ReasonCode.ROUTE_HALACHIC_INTENT)
            return Flow.HALACHIC, confidence, reason_codes
        
        if search_score == max_score:
            if search_score > 0:
                reason_codes.append(ReasonCode.ROUTE_SEARCH_KEYWORDS)
            reason_codes.append(ReasonCode.ROUTE_SEARCH_INTENT)
            return Flow.SEARCH, confidence, reason_codes
        
        # General wins
        if general_score > 0:
            reason_codes.append(ReasonCode.ROUTE_GENERAL_LEARNING)
        reason_codes.append(ReasonCode.ROUTE_GENERAL_INTENT)
        return Flow.GENERAL, confidence, reason_codes
    
    def _determine_session_action(
        self,
        flow: Flow,
        previous_flow: Optional[str],
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
        return PromptBundle(
            core_prompt_id="bt_prompt_core",
            core_prompt_version="stable",
            flow_prompt_id=f"bt_prompt_{flow.value.lower()}",
            flow_prompt_version="stable",
        )
    
    def _select_tools(self, flow: Flow) -> List[str]:
        """Select tools based on flow."""
        # Tool names that will be filtered from the full tool list
        if flow == Flow.HALACHIC:
            return [
                "get_text",
                "text_search",
                "english_semantic_search",
                "get_topic_details",
                "get_links_between_texts",
                "search_in_book",
                "clarify_name_argument",
            ]
        
        if flow == Flow.SEARCH:
            return [
                "get_text",
                "text_search",
                "english_semantic_search",
                "search_in_book",
                "search_in_dictionaries",
                "get_links_between_texts",
                "get_text_or_category_shape",
                "get_text_catalogue_info",
                "clarify_name_argument",
                "clarify_search_path_filter",
            ]
        
        if flow == Flow.GENERAL:
            return [
                "get_text",
                "text_search",
                "english_semantic_search",
                "get_topic_details",
                "get_current_calendar",
            ]
        
        # REFUSE flow - no tools
        return []
    
    def _create_refuse_result(
        self,
        decision_id: str,
        reason_codes: List[ReasonCode],
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


def get_router_service() -> RouterService:
    """Get or create the default router service."""
    global _default_router
    if _default_router is None:
        _default_router = RouterService()
    return _default_router


