from __future__ import annotations

import asyncio
import logging

from ..router import get_router_service
from .contracts import ConversationMessage


class Router:
    def __init__(self, *, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("chat.agent")

    async def run_router(
        self, bt_span, user_message: str, messages: list[ConversationMessage]
    ) -> tuple[str | None, str, list[ConversationMessage], dict]:
        """Run the router to classify the message and select the appropriate prompt.

        Returns (core_prompt_id_override, route, possibly_updated_messages, usage_dict).
        Fails open: errors default to (None, "discovery", original_messages, zero_usage).
        """
        zero_usage = {"input_tokens": 0, "output_tokens": 0, "model": ""}
        router_span = bt_span.start_span(name="router", type="task")
        try:
            router_result = await asyncio.to_thread(get_router_service().classify, user_message)
            router_span.log(
                input={"message": user_message},
                output={
                    "route": router_result.route.value,
                    "core_prompt_id": router_result.core_prompt_id,
                    "rewritten_message": router_result.rewritten_message,
                },
            )

            # If the router rewrote the message, replace the last user message
            if router_result.rewritten_message:
                updated = list(messages)
                for i in range(len(updated) - 1, -1, -1):
                    if updated[i].role == "user":
                        updated[i] = ConversationMessage(
                            role="user", content=router_result.rewritten_message
                        )
                        break
                messages = updated

            usage = {
                "input_tokens": router_result.input_tokens,
                "output_tokens": router_result.output_tokens,
                "model": router_result.model,
            }
            return router_result.core_prompt_id, router_result.route.value, messages, usage
        except Exception as exc:
            self.logger.error(f"Router failed, defaulting to Discovery: {exc}")
            router_span.log(
                input={"message": user_message},
                output={"route": "discovery", "error": str(exc)},
            )
            return None, "discovery", messages, zero_usage
        finally:
            router_span.end()
