# OpenAI-Compatible Endpoint Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `/api/v1/chat/completions` endpoint that wraps the existing agent for Braintrust playground integration.

**Architecture:** Thin adapter layer that translates OpenAI chat completion format to internal format, calls existing agent services, and transforms response back to OpenAI format. No changes to core agent logic.

**Tech Stack:** Django REST Framework, pytest, existing ClaudeAgentService and RouterService.

---

## Task 1: Create Test File with Request Validation Tests

**Files:**
- Create: `chat/tests/test_openai_compat.py`

**Step 1: Create test file with validation tests**

```python
"""Tests for OpenAI-compatible chat completions endpoint."""

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def valid_openai_request():
    return {
        "model": "sefaria-agent",
        "messages": [
            {"role": "user", "content": "What is Shabbat?"}
        ]
    }


class TestOpenAICompatValidation:
    """Test request validation for OpenAI-compatible endpoint."""

    def test_rejects_missing_messages(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent"},
            format="json"
        )
        assert response.status_code == 400
        assert "error" in response.json()
        assert response.json()["error"]["type"] == "invalid_request_error"

    def test_rejects_empty_messages(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": []},
            format="json"
        )
        assert response.status_code == 400
        assert "error" in response.json()

    def test_rejects_invalid_message_format(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": ["not a dict"]},
            format="json"
        )
        assert response.status_code == 400

    def test_rejects_message_missing_content(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": [{"role": "user"}]},
            format="json"
        )
        assert response.status_code == 400

    def test_rejects_message_missing_role(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": [{"content": "hello"}]},
            format="json"
        )
        assert response.status_code == 400
```

**Step 2: Run tests to verify they fail**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py -v`

Expected: FAIL with 404 (endpoint doesn't exist yet)

**Step 3: Commit test file**

```bash
git add chat/tests/test_openai_compat.py
git commit -m "test: add validation tests for OpenAI-compat endpoint"
```

---

## Task 2: Add Serializer for OpenAI Request Validation

**Files:**
- Modify: `chat/serializers.py`
- Test: `chat/tests/test_openai_compat.py`

**Step 1: Add serializer tests to test file**

Add to `chat/tests/test_openai_compat.py`:

```python
from chat.serializers import OpenAIMessageSerializer, OpenAIChatRequestSerializer


class TestOpenAISerializers:
    """Test OpenAI format serializers."""

    def test_message_serializer_valid(self):
        serializer = OpenAIMessageSerializer(data={"role": "user", "content": "Hello"})
        assert serializer.is_valid()

    def test_message_serializer_missing_role(self):
        serializer = OpenAIMessageSerializer(data={"content": "Hello"})
        assert not serializer.is_valid()
        assert "role" in serializer.errors

    def test_message_serializer_missing_content(self):
        serializer = OpenAIMessageSerializer(data={"role": "user"})
        assert not serializer.is_valid()
        assert "content" in serializer.errors

    def test_message_serializer_invalid_role(self):
        serializer = OpenAIMessageSerializer(data={"role": "invalid", "content": "Hello"})
        assert not serializer.is_valid()

    def test_request_serializer_valid(self):
        serializer = OpenAIChatRequestSerializer(data={
            "model": "sefaria-agent",
            "messages": [{"role": "user", "content": "Hello"}]
        })
        assert serializer.is_valid()

    def test_request_serializer_defaults_model(self):
        serializer = OpenAIChatRequestSerializer(data={
            "messages": [{"role": "user", "content": "Hello"}]
        })
        assert serializer.is_valid()
        assert serializer.validated_data["model"] == "sefaria-agent"

    def test_request_serializer_rejects_empty_messages(self):
        serializer = OpenAIChatRequestSerializer(data={
            "model": "sefaria-agent",
            "messages": []
        })
        assert not serializer.is_valid()
        assert "messages" in serializer.errors
```

**Step 2: Run tests to verify they fail**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py::TestOpenAISerializers -v`

Expected: FAIL with ImportError (serializers don't exist yet)

**Step 3: Implement serializers**

Add to `chat/serializers.py`:

```python
class OpenAIMessageSerializer(serializers.Serializer):
    """Single message in OpenAI chat format."""

    role = serializers.ChoiceField(choices=["system", "user", "assistant"])
    content = serializers.CharField(max_length=50000)


class OpenAIChatRequestSerializer(serializers.Serializer):
    """OpenAI-compatible chat completion request."""

    model = serializers.CharField(max_length=100, default="sefaria-agent")
    messages = serializers.ListField(
        child=OpenAIMessageSerializer(),
        min_length=1,
        max_length=100
    )
```

**Step 4: Run tests to verify they pass**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py::TestOpenAISerializers -v`

Expected: PASS

**Step 5: Commit**

```bash
git add chat/serializers.py chat/tests/test_openai_compat.py
git commit -m "feat: add OpenAI chat request serializers"
```

---

## Task 3: Add Minimal Endpoint with Validation

**Files:**
- Modify: `chat/views.py`
- Modify: `chat/urls.py`
- Test: `chat/tests/test_openai_compat.py`

**Step 1: Run validation tests to confirm they still fail**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py::TestOpenAICompatValidation -v`

Expected: FAIL with 404

**Step 2: Add minimal view function**

Add to `chat/views.py`:

```python
from .serializers import OpenAIChatRequestSerializer


def _openai_error_response(message: str, error_type: str, code: str, status_code: int):
    """Return an OpenAI-style error response."""
    return Response(
        {
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
            }
        },
        status=status_code,
    )


@api_view(["POST"])
def openai_chat_completions(request):
    """
    OpenAI-compatible chat completions endpoint for Braintrust integration.

    POST /api/v1/chat/completions
    """
    serializer = OpenAIChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        first_error = next(iter(serializer.errors.values()))[0]
        return _openai_error_response(
            message=f"Invalid request: {first_error}",
            error_type="invalid_request_error",
            code="invalid_request",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Placeholder - will implement full logic in next task
    return Response({"status": "not_implemented"}, status=501)
```

**Step 3: Add URL route**

Modify `chat/urls.py`:

```python
urlpatterns = [
    # Core chat endpoints
    path("chat", views.chat, name="chat"),
    path("chat/stream", views.chat_stream, name="chat_stream"),
    path("history", views.history, name="history"),
    # OpenAI-compatible endpoint for Braintrust
    path("v1/chat/completions", views.openai_chat_completions, name="openai_chat_completions"),
    # Admin/management endpoints
    path("admin/reload-prompts", views.reload_prompts, name="reload_prompts"),
    path("health", views.health, name="health"),
]
```

**Step 4: Run validation tests to verify they pass**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py::TestOpenAICompatValidation -v`

Expected: PASS

**Step 5: Commit**

```bash
git add chat/views.py chat/urls.py
git commit -m "feat: add OpenAI-compat endpoint with request validation"
```

---

## Task 4: Add Response Transformation Tests and Implementation

**Files:**
- Modify: `chat/views.py`
- Test: `chat/tests/test_openai_compat.py`

**Step 1: Add response transformation tests**

Add to `chat/tests/test_openai_compat.py`:

```python
from unittest.mock import MagicMock, patch
from chat.agent import AgentResponse
from chat.router import RouteResult, FlowType, ReasonCode, SessionAction
from chat.router.ai_guardrails import SafetyResult
from chat.prompts import PromptBundle


@pytest.fixture
def mock_agent_response():
    return AgentResponse(
        content="Shabbat is the Jewish day of rest...",
        tool_calls=[],
        llm_calls=1,
        input_tokens=150,
        output_tokens=280,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        was_refused=False,
    )


@pytest.fixture
def mock_route_result():
    return RouteResult(
        flow=FlowType.GENERAL,
        confidence=0.92,
        reason_codes=[ReasonCode.GENERAL_LEARNING],
        decision_id="route-test123",
        prompt_bundle=PromptBundle(
            core_prompt="core",
            flow_prompt="flow",
            core_prompt_id="core-id",
            core_prompt_version="v1",
            flow_prompt_id="flow-id",
            flow_prompt_version="v1",
        ),
        tools=["text_search"],
        session_action=SessionAction.CONTINUE,
        safety=SafetyResult(allowed=True),
        router_latency_ms=50,
    )


@pytest.mark.django_db
class TestOpenAICompatResponse:
    """Test response transformation for OpenAI-compatible endpoint."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_returns_openai_format_structure(
        self, mock_summary, mock_router, mock_agent, api_client, valid_openai_request, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert data["object"] == "chat.completion"
        assert "created" in data
        assert data["model"] == "sefaria-agent"
        assert "choices" in data
        assert len(data["choices"]) == 1

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_includes_usage_tokens(
        self, mock_summary, mock_router, mock_agent, api_client, valid_openai_request, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        data = response.json()
        assert "usage" in data
        assert data["usage"]["prompt_tokens"] == 150
        assert data["usage"]["completion_tokens"] == 280
        assert data["usage"]["total_tokens"] == 430

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_includes_routing_metadata(
        self, mock_summary, mock_router, mock_agent, api_client, valid_openai_request, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        data = response.json()
        assert "routing" in data
        assert data["routing"]["flow"] == "GENERAL"
        assert data["routing"]["confidence"] == 0.92
        assert data["routing"]["decision_id"] == "route-test123"
        assert data["routing"]["was_refused"] is False

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_maps_content_to_choices(
        self, mock_summary, mock_router, mock_agent, api_client, valid_openai_request, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        data = response.json()
        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == "Shabbat is the Jewish day of rest..."
        assert choice["finish_reason"] == "stop"
```

**Step 2: Run tests to verify they fail**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py::TestOpenAICompatResponse -v`

Expected: FAIL with 501 (not implemented)

**Step 3: Implement full endpoint logic**

Replace the placeholder in `chat/views.py` `openai_chat_completions` function:

```python
@api_view(["POST"])
def openai_chat_completions(request):
    """
    OpenAI-compatible chat completions endpoint for Braintrust integration.

    POST /api/v1/chat/completions

    Accepts OpenAI chat completion format, calls the Sefaria agent,
    and returns response in OpenAI format with routing metadata.
    """
    import time
    import uuid

    start_time = time.time()

    # Validate request
    serializer = OpenAIChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        first_error = next(iter(serializer.errors.values()))[0]
        return _openai_error_response(
            message=f"Invalid request: {first_error}",
            error_type="invalid_request_error",
            code="invalid_request",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    messages = data["messages"]

    # Extract last user message
    user_message = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        None
    )
    if not user_message:
        return _openai_error_response(
            message="No user message found in messages array",
            error_type="invalid_request_error",
            code="missing_user_message",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Generate identifiers with bt- prefix for traceability
    session_id = f"bt-{uuid.uuid4().hex[:12]}"
    user_id = "bt-braintrust-playground"
    turn_id = ChatMessage.generate_turn_id()
    message_id = f"msg-{uuid.uuid4().hex[:12]}"

    logger.info(
        f"📨 [openai-compat] user={user_id} session={session_id[:20]}... "
        f"text={user_message[:50]}..."
    )

    # Create session
    session, _ = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults={
            "user_id": user_id,
            "last_activity": timezone.now(),
        },
    )

    # Set up Braintrust-specific context
    page_context = {
        "site": "braintrust.dev",
        "page_type": "playground",
        "page_url": "https://braintrust.dev/playground",
        "client_version": "openai-compat-1.0",
        "source": "braintrust",
    }

    # Initialize Braintrust logger
    _get_bt_logger()

    with braintrust.start_span(name="openai-compat-request", type="task") as request_span:
        request_span.log(
            input={"query": user_message},
            metadata={
                "session_id": session_id,
                "user_id": user_id,
                "turn_id": turn_id,
                **page_context,
            },
            tags=["braintrust", "openai-compat"],
        )

        # Route the message
        router = get_router()
        route_result = router.route(
            session_id=session_id,
            user_message=user_message,
            conversation_summary="",
            previous_flow=None,
            user_metadata={
                "locale": "",
                "pageUrl": page_context["page_url"],
            },
        )

        logger.info(
            f"🔀 [openai-compat] Route: flow={route_result.flow.value} "
            f"confidence={route_result.confidence:.2f}"
        )

        # Save user message
        user_msg = ChatMessage.objects.create(
            message_id=message_id,
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            role=ChatMessage.Role.USER,
            content=user_message,
            client_timestamp=timezone.now(),
            page_url=page_context["page_url"],
            locale="",
            client_version=page_context["client_version"],
            flow=route_result.flow.value,
        )

        try:
            # Build conversation from OpenAI messages
            conversation = [
                ConversationMessage(role=m["role"], content=m["content"])
                for m in messages
            ]

            # Execute agent
            agent = get_agent_service()
            agent_response = run_async(
                agent.send_message(
                    messages=conversation,
                    route_result=route_result,
                    session_id=session_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    **page_context,
                )
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Save assistant response
            response_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=session_id,
                user_id=user_id,
                turn_id=turn_id,
                role=ChatMessage.Role.ASSISTANT,
                content=agent_response.content,
                latency_ms=latency_ms,
                llm_calls=agent_response.llm_calls,
                tool_calls_count=len(agent_response.tool_calls),
                tool_calls_data=agent_response.tool_calls,
                input_tokens=agent_response.input_tokens,
                output_tokens=agent_response.output_tokens,
                cache_creation_tokens=agent_response.cache_creation_tokens,
                cache_read_tokens=agent_response.cache_read_tokens,
                model_name="claude-sonnet-4-5-20250929",
                flow=route_result.flow.value,
                status=ChatMessage.Status.REFUSED
                if agent_response.was_refused
                else ChatMessage.Status.SUCCESS,
            )

            logger.info(
                f"📤 [openai-compat] response={response_msg.message_id[:20]}... "
                f"latency={latency_ms}ms tokens={agent_response.input_tokens}+{agent_response.output_tokens}"
            )

            request_span.log(
                output={"response": agent_response.content[:500]},
                tags=[route_result.flow.value.lower(), "braintrust"],
                metrics={
                    "latency_ms": latency_ms,
                    "input_tokens": agent_response.input_tokens,
                    "output_tokens": agent_response.output_tokens,
                },
            )

            # Return OpenAI-compatible response
            return Response({
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": data["model"],
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": agent_response.content,
                    },
                    "finish_reason": "content_filter" if agent_response.was_refused else "stop",
                }],
                "usage": {
                    "prompt_tokens": agent_response.input_tokens,
                    "completion_tokens": agent_response.output_tokens,
                    "total_tokens": agent_response.input_tokens + agent_response.output_tokens,
                },
                "routing": {
                    "flow": route_result.flow.value,
                    "decision_id": route_result.decision_id,
                    "confidence": route_result.confidence,
                    "was_refused": agent_response.was_refused,
                },
            })

        except Exception as e:
            logger.error(f"❌ [openai-compat] Agent error: {e}", exc_info=True)

            request_span.log(
                output={"error": str(e)},
                error=str(e),
            )

            return _openai_error_response(
                message=f"Internal error: {str(e)}",
                error_type="internal_error",
                code="agent_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
```

**Step 4: Run tests to verify they pass**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py::TestOpenAICompatResponse -v`

Expected: PASS

**Step 5: Commit**

```bash
git add chat/views.py chat/tests/test_openai_compat.py
git commit -m "feat: implement OpenAI-compat endpoint with full agent integration"
```

---

## Task 5: Add Multi-Turn Conversation and Error Handling Tests

**Files:**
- Modify: `chat/tests/test_openai_compat.py`

**Step 1: Add remaining tests**

Add to `chat/tests/test_openai_compat.py`:

```python
@pytest.mark.django_db
class TestOpenAICompatMultiTurn:
    """Test multi-turn conversation handling."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_extracts_last_user_message(
        self, mock_summary, mock_router, mock_agent, api_client, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        response = api_client.post(
            "/api/v1/chat/completions",
            data={
                "model": "sefaria-agent",
                "messages": [
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "First answer"},
                    {"role": "user", "content": "Follow-up question"},
                ]
            },
            format="json"
        )

        assert response.status_code == 200
        # Verify the router was called with the last user message
        call_args = mock_router.return_value.route.call_args
        assert call_args.kwargs["user_message"] == "Follow-up question"

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_handles_system_message(
        self, mock_summary, mock_router, mock_agent, api_client, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        response = api_client.post(
            "/api/v1/chat/completions",
            data={
                "model": "sefaria-agent",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant"},
                    {"role": "user", "content": "What is Shabbat?"},
                ]
            },
            format="json"
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestOpenAICompatErrors:
    """Test error handling for OpenAI-compatible endpoint."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_agent_error_returns_openai_error_format(
        self, mock_router, mock_agent, api_client, valid_openai_request, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(
            side_effect=Exception("Agent failed")
        )

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["error"]["type"] == "internal_error"
        assert "Agent failed" in data["error"]["message"]


@pytest.mark.django_db
class TestOpenAICompatTraceability:
    """Test logging and traceability."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_generates_bt_prefixed_session_id(
        self, mock_summary, mock_router, mock_agent, api_client, valid_openai_request, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        # Verify session_id passed to router starts with bt-
        call_args = mock_router.return_value.route.call_args
        assert call_args.kwargs["session_id"].startswith("bt-")

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    @patch("chat.views.get_summary_service")
    def test_sets_braintrust_source_in_context(
        self, mock_summary, mock_router, mock_agent, api_client, valid_openai_request, mock_agent_response, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = MagicMock(return_value=mock_agent_response)
        mock_summary.return_value.update_summary = MagicMock()

        api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json"
        )

        # Verify agent was called with braintrust source
        call_args = mock_agent.return_value.send_message.call_args
        assert call_args.kwargs.get("source") == "braintrust"
```

**Step 2: Run all tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/tests/test_openai_compat.py -v`

Expected: PASS

**Step 3: Commit**

```bash
git add chat/tests/test_openai_compat.py
git commit -m "test: add multi-turn and error handling tests for OpenAI-compat endpoint"
```

---

## Task 6: Run Full Test Suite and Verify

**Step 1: Run all project tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest -v`

Expected: All tests PASS

**Step 2: Verify endpoint manually (optional)**

Run: `python manage.py runserver 0.0.0.0:8001`

Then in another terminal:
```bash
curl -X POST http://localhost:8001/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "sefaria-agent", "messages": [{"role": "user", "content": "What is Shabbat?"}]}'
```

Expected: OpenAI-format JSON response with routing metadata

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete OpenAI-compatible endpoint for Braintrust integration"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Create test file with validation tests | `test_openai_compat.py` |
| 2 | Add serializers for request validation | `serializers.py` |
| 3 | Add minimal endpoint with validation | `views.py`, `urls.py` |
| 4 | Implement full response transformation | `views.py` |
| 5 | Add multi-turn and error tests | `test_openai_compat.py` |
| 6 | Run full test suite | - |
