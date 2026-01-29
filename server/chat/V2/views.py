"""V2 chat API views (HTTP endpoints only)."""

import logging
import os

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..serializers import FeedbackRequestSerializer

logger = logging.getLogger("chat")

_bt_logger = None


def get_braintrust_logger():
    """Get or create a Braintrust logger for feedback."""
    global _bt_logger
    api_key = os.environ.get("BRAINTRUST_API_KEY")
    project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
    if not api_key:
        return None

    try:
        import braintrust

        _bt_logger = braintrust.init_logger(project=project, api_key=api_key)
        return _bt_logger
    except Exception as e:
        logger.warning(f"⚠️  Failed to initialize Braintrust logger: {e}")
        return None


@api_view(["GET"])
def prompt_defaults(request):
    """Return default Braintrust prompt slugs for client settings."""
    return Response(
        {
            "corePromptSlug": settings.CORE_PROMPT_SLUG,
        }
    )


@api_view(["POST"])
def chat_feedback_v2(request):
    """Capture user feedback for the latest chat trace in Braintrust."""
    serializer = FeedbackRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    bt_logger = get_braintrust_logger()
    if not bt_logger:
        return Response(
            {"error": "braintrust_unavailable"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    metadata = {
        "user_id": data.get("userId", ""),
        "session_id": data.get("sessionId", ""),
        "message_id": data.get("messageId", ""),
    }
    comment = (data.get("comment") or "").strip() or None
    scores = {"user_rating": data["score"]}

    try:
        if hasattr(bt_logger, "log_feedback"):
            bt_logger.log_feedback(
                id=data["traceId"],
                scores=scores,
                comment=comment,
                metadata=metadata,
            )
        elif hasattr(bt_logger, "logFeedback"):
            bt_logger.logFeedback(
                {
                    "id": data["traceId"],
                    "scores": scores,
                    "comment": comment,
                    "metadata": metadata,
                }
            )
        else:
            raise AttributeError("Braintrust logger does not support feedback logging")
    except Exception as e:
        logger.error(f"❌ Failed to log feedback: {e}")
        return Response({"error": "feedback_log_failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({"success": True})
