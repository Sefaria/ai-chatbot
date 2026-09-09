"""Replacements for the three services that need Braintrust.

The prompt, guardrail and router services all call Braintrust with our API key.
The daemon has no key, so it proxies each to our server, which runs the real
service and returns its result. Installed via ``CHAT_SERVICE_OVERRIDES``.

Each proxy returns the *same dataclass* the real service does, so the turn code
above them cannot tell the difference — which is what keeps local mode at parity
rather than merely similar.

Failure policy matches the services being replaced: the guardrail fails **closed**
(a message is not allowed if we could not check it) and the router fails **open**
to discovery, exactly as ``RouterService`` does on its own errors.
"""

from __future__ import annotations

import logging

import httpx

from chat.V2.guardrail.guardrail_service import GuardrailResult
from chat.V2.prompts.prompt_service import CorePrompt
from chat.V2.router.router_service import RouterResult, RouteType
from chat.V2.summarization.summary_service import SummaryService

from . import pairing
from .identity import server_base_url

logger = logging.getLogger("local_runner")

TIMEOUT_SECONDS = 30

GUARDRAIL_UNAVAILABLE = "I can't check that message right now. Please try again in a moment."


class ProxyError(RuntimeError):
    """Our server could not be reached or refused the request."""


def _post(path: str, payload: dict) -> dict:
    """Call one of the /api/v2/local endpoints as the paired user."""
    record = pairing.load()
    if record is None:
        raise ProxyError("this runner is not paired")

    body = dict(payload)
    body["userId"] = record.encrypted_user_token

    url = f"{server_base_url()}{path}"
    try:
        response = httpx.post(url, json=body, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise ProxyError(f"could not reach {url}: {exc}") from exc

    if response.status_code != 200:
        raise ProxyError(f"{path} returned {response.status_code}")

    return response.json()


class ProxyPromptService:
    """Fetches prompts from our server instead of Braintrust."""

    def get_core_prompt(
        self,
        prompt_id: str | None = None,
        version: str = "stable",
        build_vars: dict[str, str] | None = None,
    ) -> CorePrompt:
        payload = {
            "promptId": prompt_id,
            "version": version,
            "buildVars": build_vars or {},
        }
        data = _post("/api/v2/local/prompt", payload)
        return CorePrompt(
            text=data["text"],
            prompt_id=data.get("promptId") or (prompt_id or ""),
            version=data.get("version") or version,
        )


class ProxyGuardrailService:
    """Runs the guardrail on our server, where the brand-safety prompt lives."""

    def check_message(self, user_message: str) -> GuardrailResult:
        try:
            data = _post("/api/v2/local/guardrail", {"message": user_message})
        except ProxyError as exc:
            # Fails closed: an unchecked message is not an allowed one.
            logger.warning("guardrail unavailable, blocking: %s", exc)
            return GuardrailResult(allowed=False, reason=GUARDRAIL_UNAVAILABLE)

        return GuardrailResult(allowed=bool(data.get("allowed")), reason=data.get("reason", ""))


class NullAppetizerService:
    """Disables the topic appetizer locally.

    The appetizer makes its own Anthropic call with our key, so it cannot run on
    a user's machine as-is. Explicitly returning nothing beats letting the real
    service raise: the caller already tolerates a missing appetizer, and a
    disabled feature should not print a stack trace on every turn.

    Known parity gap — local turns show no topic chip. Proxying it is the fix if
    it turns out to matter.
    """

    async def find_appetizer(self, *args, **kwargs):
        metrics_sink = kwargs.get("metrics_sink")
        if isinstance(metrics_sink, dict):
            metrics_sink["source"] = "disabled_local_runner"
        return None


class ProxyRouterService:
    """Classifies messages on our server."""

    def classify(self, user_message: str) -> RouterResult:
        try:
            data = _post("/api/v2/local/route", {"message": user_message})
        except ProxyError as exc:
            # Fails open to discovery, matching RouterService's own behaviour.
            logger.warning("router unavailable, defaulting to discovery: %s", exc)
            return RouterResult(route=RouteType.DISCOVERY)

        try:
            route = RouteType(data.get("route"))
        except ValueError:
            logger.warning("unknown route %r, defaulting to discovery", data.get("route"))
            route = RouteType.DISCOVERY

        return RouterResult(
            route=route,
            core_prompt_id=data.get("corePromptId"),
            rewritten_message=data.get("rewrittenMessage"),
        )


class ProxySummaryService(SummaryService):
    """Summarizes via our server, and stores the result locally.

    Only the model call is proxied. ``_apply_summary_data`` still runs here, so
    the summary row is written to the runner's own database (D4).

    This matters more than "summaries" suggests: the agent is handed only the
    current message, so the summary carries the whole multi-turn memory. Without
    it local mode would silently become single-turn.
    """

    def __init__(self):
        # Skip SummaryService.__init__, which builds an Anthropic client we have
        # no key for. use_llm stays True because we do have a model — remotely.
        self.api_key = None
        self.client = None
        self.model = None
        self.use_llm = True
        logger.info("SummaryService initialized (proxied to the server)")

    def _llm_summarize(
        self,
        session,
        current_summary,
        new_user_message: str,
        new_assistant_response: str,
    ):
        previous = current_summary.to_prompt_text() if current_summary else ""
        try:
            data = _post(
                "/api/v2/local/summary",
                {
                    "previousSummary": previous,
                    "userMessage": new_user_message,
                    "assistantResponse": new_assistant_response,
                },
            )["summary"]
        except (ProxyError, KeyError) as exc:
            # Same fallback the real service uses when its own call fails, so a
            # server hiccup costs summary quality rather than the turn.
            logger.warning("summary unavailable, using rule-based: %s", exc)
            return self._simple_summarize(
                session, current_summary, new_user_message, new_assistant_response
            )

        return self._apply_summary_data(session=session, current_summary=current_summary, data=data)
