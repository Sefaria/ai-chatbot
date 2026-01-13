"""
Guardrail checker for content safety and product boundaries.

Implements rule-based detection for:
- Prompt injection attempts
- High-risk halachic questions
- Disallowed content categories
- Privacy/PII concerns
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .reason_codes import ReasonCode

logger = logging.getLogger('chat.router.guardrails')


@dataclass
class GuardrailResult:
    """Result of guardrail check."""
    allowed: bool = True
    reason_codes: List[ReasonCode] = field(default_factory=list)
    refusal_message: Optional[str] = None
    confidence: float = 1.0


# Patterns for prompt injection detection
PROMPT_INJECTION_PATTERNS = [
    # Direct instruction override attempts
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+)?(previous|prior|above)",
    r"forget\s+(everything|all)\s+(you\s+)?were\s+told",
    r"new\s+instructions?\s*:",
    r"system\s*:\s*you\s+are\s+now",
    r"act\s+as\s+if\s+you\s+have\s+no\s+(rules|restrictions|guidelines)",
    
    # Role play exploitation
    r"pretend\s+(you\s+are|to\s+be)\s+a\s+different\s+(ai|assistant|bot)",
    r"roleplay\s+as\s+(an\s+)?unrestricted",
    r"jailbreak",
    r"dan\s+mode",
    
    # System prompt extraction
    r"(reveal|show|display|print|output)\s+(your\s+)?(system\s+)?(prompt|instructions)",
    r"what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions|rules)",
    r"tell\s+me\s+(your\s+)?hidden\s+(prompt|instructions)",
    r"repeat\s+(your\s+)?(initial|first|system)\s+(prompt|message|instructions)",
]

# Halachic high-risk patterns (require disclaimer or referral)
HIGH_RISK_HALACHIC_PATTERNS = [
    # Life and death matters
    r"(pikuach\s+nefesh|life\s+threatening|danger\s+to\s+life)",
    r"(abortion|terminate\s+pregnancy)",
    r"(end\s+of\s+life|euthanasia|dying\s+patient)",
    
    # Marriage/divorce
    r"(get|gittin|divorce\s+proceeding)",
    r"(agunah|chained\s+woman)",
    r"(kiddushin|marriage\s+ceremony|wedding\s+officiate)",
    
    # Conversion
    r"(gerut|conversion\s+to\s+judaism|become\s+jewish)",
    
    # Monetary disputes
    r"(beis\s+din|bet\s+din|rabbinical\s+court)",
    r"(monetary\s+dispute|financial\s+claim)",
]

# Medical/legal advice patterns
MEDICAL_LEGAL_PATTERNS = [
    r"(should\s+i\s+take|what\s+medication)",
    r"(diagnose|diagnosis|symptoms?\s+of)",
    r"(sue|lawsuit|legal\s+action|attorney)",
    r"(medical\s+advice|doctor\s+recommend)",
]

# Harassment/abuse patterns
HARASSMENT_PATTERNS = [
    r"(stupid|idiot|moron|dumb)\s+(jew|jewish|rabbi)",
    r"(hate|kill|destroy)\s+(all\s+)?(jews|jewish|israel)",
    r"(hitler|nazi|holocaust)\s+(was\s+)?(right|good)",
]


class GuardrailChecker:
    """
    Checks messages against safety guardrails.

    Uses rule-based pattern matching for fast, deterministic checks.
    This serves as the fallback for the AI-based guardrail checker.
    """

    def __init__(self, strict_mode: bool = False):
        """
        Initialize the guardrail checker.

        Args:
            strict_mode: If True, err on the side of caution for edge cases
        """
        self.strict_mode = strict_mode

        # Compile patterns for efficiency
        self._injection_patterns = [
            re.compile(p, re.IGNORECASE) for p in PROMPT_INJECTION_PATTERNS
        ]
        self._high_risk_patterns = [
            re.compile(p, re.IGNORECASE) for p in HIGH_RISK_HALACHIC_PATTERNS
        ]
        self._medical_legal_patterns = [
            re.compile(p, re.IGNORECASE) for p in MEDICAL_LEGAL_PATTERNS
        ]
        self._harassment_patterns = [
            re.compile(p, re.IGNORECASE) for p in HARASSMENT_PATTERNS
        ]
    
    def check(self, message: str, context: Optional[dict] = None) -> GuardrailResult:
        """
        Check a message against all guardrails.
        
        Args:
            message: The user message to check
            context: Optional context (conversation summary, user metadata, etc.)
            
        Returns:
            GuardrailResult with allowed status, reason codes, and optional refusal message
        """
        result = GuardrailResult()
        
        # Check in order of severity
        
        # 1. Harassment/hate speech (hard block)
        harassment_result = self._check_harassment(message)
        if not harassment_result.allowed:
            return harassment_result
        result.reason_codes.extend(harassment_result.reason_codes)
        
        # 2. Prompt injection (hard block)
        injection_result = self._check_prompt_injection(message)
        if not injection_result.allowed:
            return injection_result
        result.reason_codes.extend(injection_result.reason_codes)
        
        # 3. High-risk halachic (soft warning, add disclaimer)
        high_risk_result = self._check_high_risk_halachic(message)
        result.reason_codes.extend(high_risk_result.reason_codes)
        
        # 4. Medical/legal advice (soft warning)
        medical_legal_result = self._check_medical_legal(message)
        result.reason_codes.extend(medical_legal_result.reason_codes)
        
        return result
    
    def _check_prompt_injection(self, message: str) -> GuardrailResult:
        """Check for prompt injection attempts."""
        for pattern in self._injection_patterns:
            if pattern.search(message):
                logger.warning(f"Prompt injection detected: {pattern.pattern[:50]}...")
                return GuardrailResult(
                    allowed=False,
                    reason_codes=[ReasonCode.GUARDRAIL_PROMPT_INJECTION],
                    refusal_message="I'm designed to help with Jewish learning. I can't process requests that attempt to modify my instructions.",
                    confidence=0.9
                )
        
        # Check for system prompt leak requests
        system_leak_patterns = [
            r"what\s+are\s+your\s+instructions",
            r"show\s+me\s+your\s+prompt",
            r"reveal\s+your\s+system",
        ]
        for pattern_str in system_leak_patterns:
            if re.search(pattern_str, message, re.IGNORECASE):
                return GuardrailResult(
                    allowed=False,
                    reason_codes=[ReasonCode.GUARDRAIL_SYSTEM_PROMPT_LEAK],
                    refusal_message="I'm here to help with Jewish learning and texts. What would you like to explore?",
                    confidence=0.85
                )
        
        return GuardrailResult()
    
    def _check_harassment(self, message: str) -> GuardrailResult:
        """Check for harassment or hate speech."""
        for pattern in self._harassment_patterns:
            if pattern.search(message):
                logger.warning(f"Harassment pattern detected")
                return GuardrailResult(
                    allowed=False,
                    reason_codes=[ReasonCode.GUARDRAIL_HARASSMENT, ReasonCode.GUARDRAIL_HATE_SPEECH],
                    refusal_message="I can't engage with messages containing hate speech or harassment. I'm here to help with respectful learning.",
                    confidence=0.95
                )
        
        return GuardrailResult()
    
    def _check_high_risk_halachic(self, message: str) -> GuardrailResult:
        """Check for high-risk halachic questions."""
        result = GuardrailResult()
        
        for pattern in self._high_risk_patterns:
            if pattern.search(message):
                result.reason_codes.append(ReasonCode.GUARDRAIL_HIGH_RISK_PSAK)
                result.reason_codes.append(ReasonCode.GUARDRAIL_REQUIRES_AUTHORITY)
                break
        
        return result
    
    def _check_medical_legal(self, message: str) -> GuardrailResult:
        """Check for medical or legal advice requests."""
        result = GuardrailResult()
        
        for pattern in self._medical_legal_patterns:
            if pattern.search(message):
                result.reason_codes.append(ReasonCode.GUARDRAIL_MEDICAL_ADVICE)
                break
        
        return result


# Default guardrail checker instance
_default_checker = None
_default_ai_checker = None


def get_guardrail_checker(use_ai: bool = False) -> GuardrailChecker:
    """
    Get or create the default guardrail checker.

    Args:
        use_ai: If True, return AI-based checker with rule-based fallback.
                If False, return rule-based checker only.

    Returns:
        GuardrailChecker (rule-based) or AIGuardrailChecker (AI-based)
    """
    global _default_checker, _default_ai_checker

    if use_ai:
        if _default_ai_checker is None:
            try:
                from .ai_guardrails import get_ai_guardrail_checker

                # Create rule-based checker as fallback
                if _default_checker is None:
                    _default_checker = GuardrailChecker()

                # Create AI checker with fallback
                _default_ai_checker = get_ai_guardrail_checker(
                    fallback_checker=_default_checker
                )
                logger.info("Using AI-based guardrail checker with rule-based fallback")
            except Exception as e:
                logger.error(f"Failed to initialize AI guardrail checker: {e}")
                logger.info("Falling back to rule-based guardrail checker")
                if _default_checker is None:
                    _default_checker = GuardrailChecker()
                return _default_checker

        return _default_ai_checker
    else:
        if _default_checker is None:
            _default_checker = GuardrailChecker()
        return _default_checker


