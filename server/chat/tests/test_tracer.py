"""Tests for the observability tracer abstraction.

This module tests the tracer interface that abstracts away Braintrust-specific
code, enabling:
1. Dual logging to Braintrust + database
2. Swapping providers without code changes
3. Testing without external dependencies

The tracer should capture all data that is currently sent to Braintrust spans.
"""

import asyncio
import contextvars
import time


class TestSpanDataCapture:
    """Test that spans capture all the data we need for logging."""

    def test_span_captures_input_on_log(self) -> None:
        """Span should capture input data passed to log()."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(input={"query": "What is Shabbat?"})

        assert span.data.input == {"query": "What is Shabbat?"}

    def test_span_captures_output_on_log(self) -> None:
        """Span should capture output data passed to log()."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(output={"response": "Shabbat is the day of rest."})

        assert span.data.output == {"response": "Shabbat is the day of rest."}

    def test_span_captures_metadata_on_log(self) -> None:
        """Span should capture metadata passed to log()."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(metadata={"session_id": "abc123", "user_id": "user1"})

        assert span.data.metadata == {"session_id": "abc123", "user_id": "user1"}

    def test_span_captures_metrics_on_log(self) -> None:
        """Span should capture metrics passed to log()."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(metrics={"latency_ms": 150, "tokens": 100})

        assert span.data.metrics == {"latency_ms": 150, "tokens": 100}

    def test_span_captures_tags_on_log(self) -> None:
        """Span should capture tags passed to log()."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(tags=["dev", "halachic"])

        assert span.data.tags == ["dev", "halachic"]

    def test_span_captures_error_on_log(self) -> None:
        """Span should capture error string passed to log()."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(error="Connection failed")

        assert span.data.error == "Connection failed"

    def test_span_accumulates_multiple_log_calls(self) -> None:
        """Multiple log() calls should accumulate data, not replace.

        This is important because we often call:
        span.log(input=...) at start
        span.log(output=..., metrics=...) at end
        """
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(input={"query": "test"})
            span.log(output={"response": "answer"})
            span.log(metrics={"latency_ms": 100})

        assert span.data.input == {"query": "test"}
        assert span.data.output == {"response": "answer"}
        assert span.data.metrics == {"latency_ms": 100}

    def test_span_merges_tags_from_multiple_calls(self) -> None:
        """Tags from multiple log() calls should be merged."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(tags=["dev"])
            span.log(tags=["halachic"])

        assert set(span.data.tags) == {"dev", "halachic"}

    def test_span_merges_metadata_from_multiple_calls(self) -> None:
        """Metadata from multiple log() calls should be merged."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(metadata={"session_id": "abc"})
            span.log(metadata={"user_id": "user1"})

        assert span.data.metadata == {"session_id": "abc", "user_id": "user1"}

    def test_span_merges_metrics_from_multiple_calls(self) -> None:
        """Metrics from multiple log() calls should be merged."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(metrics={"latency_ms": 100})
            span.log(metrics={"tokens": 50})

        assert span.data.metrics == {"latency_ms": 100, "tokens": 50}


class TestStartSpan:
    """Test the start_span context manager."""

    def test_start_span_returns_span_in_context(self) -> None:
        """start_span should yield a Span object."""
        from chat.observability.tracer import Span, start_span

        with start_span(name="test", type="task") as span:
            assert isinstance(span, Span)

    def test_start_span_records_name_and_type(self) -> None:
        """Span should have name and type from start_span."""
        from chat.observability.tracer import start_span

        with start_span(name="request", type="task") as span:
            assert span.name == "request"
            assert span.span_type == "task"

    def test_start_span_records_start_time(self) -> None:
        """Span should record when it started."""
        from chat.observability.tracer import start_span

        before = time.time()
        with start_span(name="test", type="task") as span:
            after = time.time()
            assert before <= span.start_time <= after

    def test_span_end_records_duration(self) -> None:
        """Exiting context should record duration."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            time.sleep(0.01)  # 10ms

        assert span.duration_ms is not None
        assert span.duration_ms >= 10

    def test_nested_spans_track_parent(self) -> None:
        """Nested start_span calls should create parent-child relationship."""
        from chat.observability.tracer import start_span

        with start_span(name="parent", type="task") as parent:
            with start_span(name="child", type="llm") as child:
                assert child.parent_id == parent.span_id

    def test_span_has_unique_id(self) -> None:
        """Each span should have a unique span_id."""
        from chat.observability.tracer import start_span

        with start_span(name="span1", type="task") as span1:
            pass
        with start_span(name="span2", type="task") as span2:
            pass

        assert span1.span_id != span2.span_id


class TestCurrentSpan:
    """Test the current_span() function for getting active span."""

    def test_current_span_returns_active_span(self) -> None:
        """current_span() should return the span from start_span context."""
        from chat.observability.tracer import current_span, start_span

        with start_span(name="test", type="task") as span:
            assert current_span() is span

    def test_current_span_returns_none_outside_context(self) -> None:
        """current_span() should return None when not in a span context."""
        from chat.observability.tracer import current_span

        assert current_span() is None

    def test_current_span_returns_innermost_in_nested(self) -> None:
        """In nested spans, current_span() should return the innermost."""
        from chat.observability.tracer import current_span, start_span

        with start_span(name="outer", type="task") as outer:
            assert current_span() is outer
            with start_span(name="inner", type="llm") as inner:
                assert current_span() is inner
            # After exiting inner, should return to outer
            assert current_span() is outer


class TestTracedDecorator:
    """Test the @traced decorator for automatic span creation."""

    def test_traced_creates_span_for_sync_function(self) -> None:
        """@traced should create a span around sync function calls."""
        from chat.observability.tracer import current_span, traced

        captured_span = None

        @traced(name="my_func", type="function")
        def my_function():
            nonlocal captured_span
            captured_span = current_span()
            return "result"

        result = my_function()

        assert result == "result"
        assert captured_span is not None
        assert captured_span.name == "my_func"

    def test_traced_creates_span_for_async_function(self) -> None:
        """@traced should create a span around async function calls."""
        from chat.observability.tracer import current_span, traced

        captured_span = None

        @traced(name="my_async_func", type="function")
        async def my_async_function():
            nonlocal captured_span
            captured_span = current_span()
            return "async result"

        result = asyncio.run(my_async_function())

        assert result == "async result"
        assert captured_span is not None
        assert captured_span.name == "my_async_func"

    def test_traced_preserves_function_name(self) -> None:
        """@traced should preserve the original function's __name__."""
        from chat.observability.tracer import traced

        @traced(name="span_name", type="function")
        def original_name():
            pass

        assert original_name.__name__ == "original_name"

    def test_traced_preserves_function_docstring(self) -> None:
        """@traced should preserve the original function's docstring."""
        from chat.observability.tracer import traced

        @traced(name="span_name", type="function")
        def documented_function():
            """This is the docstring."""
            pass

        assert documented_function.__doc__ == "This is the docstring."

    def test_current_span_works_inside_traced_function(self) -> None:
        """current_span() should work inside a @traced function."""
        from chat.observability.tracer import current_span, traced

        @traced(name="outer", type="function")
        def outer_function():
            span = current_span()
            span.log(input={"test": "value"})
            return span

        span = outer_function()
        assert span.data.input == {"test": "value"}

    def test_traced_spans_nest_under_parent(self) -> None:
        """Spans from @traced should nest under parent spans."""
        from chat.observability.tracer import current_span, start_span, traced

        child_span = None

        @traced(name="child", type="function")
        def child_function():
            nonlocal child_span
            child_span = current_span()

        with start_span(name="parent", type="task") as parent:
            child_function()

        assert child_span.parent_id == parent.span_id


class TestSpanHierarchy:
    """Test span parent-child relationships and nesting."""

    def test_root_span_has_no_parent(self) -> None:
        """Top-level span should have parent_id=None."""
        from chat.observability.tracer import start_span

        with start_span(name="root", type="task") as span:
            assert span.parent_id is None

    def test_nested_span_has_parent_id(self) -> None:
        """Nested span should reference parent span's id."""
        from chat.observability.tracer import start_span

        with start_span(name="parent", type="task") as parent:
            with start_span(name="child", type="llm") as child:
                assert child.parent_id == parent.span_id

    def test_trace_id_propagates_to_children(self) -> None:
        """All spans in a trace should share the same trace_id."""
        from chat.observability.tracer import start_span

        with start_span(name="root", type="task") as root:
            with start_span(name="child", type="llm") as child:
                with start_span(name="grandchild", type="tool") as grandchild:
                    assert child.trace_id == root.trace_id
                    assert grandchild.trace_id == root.trace_id


class TestSpanExport:
    """Test exporting span data to different backends."""

    def test_span_to_dict_includes_all_fields(self) -> None:
        """to_dict() should include all captured data."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(
                input={"query": "test"},
                output={"response": "answer"},
                metadata={"session_id": "abc"},
                metrics={"latency_ms": 100},
                tags=["dev"],
            )

        data = span.to_dict()

        assert data["name"] == "test"
        assert data["type"] == "task"
        assert data["input"] == {"query": "test"}
        assert data["output"] == {"response": "answer"}
        assert data["metadata"] == {"session_id": "abc"}
        assert data["metrics"] == {"latency_ms": 100}
        assert data["tags"] == ["dev"]
        assert "span_id" in data
        assert "start_time" in data
        assert "duration_ms" in data

    def test_span_to_dict_format_matches_expected_schema(self) -> None:
        """to_dict() should produce a consistent schema for DB storage."""
        from chat.observability.tracer import start_span

        with start_span(name="test", type="task") as span:
            span.log(input={"query": "test"})

        data = span.to_dict()

        # Required fields
        required_fields = [
            "span_id",
            "trace_id",
            "parent_id",
            "name",
            "type",
            "start_time",
            "duration_ms",
            "input",
            "output",
            "metadata",
            "metrics",
            "tags",
            "error",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"


class TestTracerBackend:
    """Test tracer with backends to verify what gets sent."""

    def test_backend_receives_span_on_end(self) -> None:
        """Backend's record_span should be called when span ends."""
        from chat.observability.tracer import Tracer

        recorded_spans = []

        class MockBackend:
            def record_span(self, span_data: dict):
                recorded_spans.append(span_data)

        tracer = Tracer(backends=[MockBackend()])

        with tracer.start_span(name="test", type="task") as span:
            span.log(input={"query": "test"})

        assert len(recorded_spans) == 1
        assert recorded_spans[0]["name"] == "test"

    def test_backend_receives_complete_span_data(self) -> None:
        """Backend should receive all accumulated span data."""
        from chat.observability.tracer import Tracer

        recorded_spans = []

        class MockBackend:
            def record_span(self, span_data: dict):
                recorded_spans.append(span_data)

        tracer = Tracer(backends=[MockBackend()])

        with tracer.start_span(name="test", type="task") as span:
            span.log(input={"query": "test"})
            span.log(output={"response": "answer"})
            span.log(metrics={"latency_ms": 100})

        data = recorded_spans[0]
        assert data["input"] == {"query": "test"}
        assert data["output"] == {"response": "answer"}
        assert data["metrics"] == {"latency_ms": 100}

    def test_multiple_backends_all_receive_data(self) -> None:
        """With multiple backends, all should receive span data."""
        from chat.observability.tracer import Tracer

        backend1_spans = []
        backend2_spans = []

        class Backend1:
            def record_span(self, span_data: dict):
                backend1_spans.append(span_data)

        class Backend2:
            def record_span(self, span_data: dict):
                backend2_spans.append(span_data)

        tracer = Tracer(backends=[Backend1(), Backend2()])

        with tracer.start_span(name="test", type="task") as span:
            span.log(input={"query": "test"})

        assert len(backend1_spans) == 1
        assert len(backend2_spans) == 1
        assert backend1_spans[0]["name"] == "test"
        assert backend2_spans[0]["name"] == "test"


class TestContextPropagation:
    """Test that span context propagates correctly across threads."""

    def test_context_preserved_with_copy_context(self) -> None:
        """Using contextvars.copy_context() should preserve span context."""
        from chat.observability.tracer import current_span, start_span

        captured_span_in_thread = None

        with start_span(name="parent", type="task") as parent:
            ctx = contextvars.copy_context()

            def run_in_thread():
                nonlocal captured_span_in_thread
                captured_span_in_thread = current_span()

            # Run with captured context
            ctx.run(run_in_thread)

        assert captured_span_in_thread is parent


# =============================================================================
# Expected Data Structure Tests
# =============================================================================
# These tests verify the data captured matches what is currently sent to
# Braintrust. They serve as a specification and regression tests.


class TestExpectedRequestSpanData:
    """Test the data structure for request-level spans.

    Based on orchestrator.py prepare_turn() and complete_turn().
    """

    def test_request_span_input_structure(self) -> None:
        """Request span input should match what prepare_turn logs.

        Expected:
            input = {"query": user_message}
        """
        from chat.observability.tracer import start_span

        with start_span(name="request", type="task") as span:
            # Simulate what prepare_turn does
            span.log(input={"query": "What is Shabbat?"})

        assert "query" in span.data.input
        assert isinstance(span.data.input["query"], str)

    def test_request_span_metadata_structure(self) -> None:
        """Request span metadata should match what prepare_turn logs.

        Expected:
            metadata = {
                "session_id": str,
                "user_id": str,
                "turn_id": str,
                "site": str,
                "page_type": str,
                "page_url": str,
                "client_version": str,
                "source": str,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="request", type="task") as span:
            span.log(
                metadata={
                    "session_id": "sess_123",
                    "user_id": "user_456",
                    "turn_id": "turn_789",
                    "site": "www.sefaria.org",
                    "page_type": "reader",
                    "page_url": "https://www.sefaria.org/Genesis.1",
                    "client_version": "1.0.0",
                    "source": "component",
                }
            )

        expected_keys = {
            "session_id",
            "user_id",
            "turn_id",
            "site",
            "page_type",
            "page_url",
            "client_version",
            "source",
        }
        assert expected_keys <= set(span.data.metadata.keys())

    def test_request_span_output_structure(self) -> None:
        """Request span output should match what complete_turn logs.

        Expected:
            output = {"response": str}
        """
        from chat.observability.tracer import start_span

        with start_span(name="request", type="task") as span:
            span.log(output={"response": "Shabbat is the day of rest..."})

        assert "response" in span.data.output
        assert isinstance(span.data.output["response"], str)

    def test_request_span_metrics_structure(self) -> None:
        """Request span metrics should match what complete_turn logs.

        Expected:
            metrics = {
                "latency_ms": int,
                "llm_calls": int,
                "tool_calls": int,
                "prompt_tokens": int,
                "completion_tokens": int,
                "prompt_cache_creation_tokens": int,
                "prompt_cached_tokens": int,
                "tokens": int,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="request", type="task") as span:
            span.log(
                metrics={
                    "latency_ms": 1500,
                    "llm_calls": 3,
                    "tool_calls": 2,
                    "prompt_tokens": 500,
                    "completion_tokens": 200,
                    "prompt_cache_creation_tokens": 100,
                    "prompt_cached_tokens": 50,
                    "tokens": 850,
                }
            )

        expected_keys = {
            "latency_ms",
            "llm_calls",
            "tool_calls",
            "prompt_tokens",
            "completion_tokens",
            "prompt_cache_creation_tokens",
            "prompt_cached_tokens",
            "tokens",
        }
        assert expected_keys <= set(span.data.metrics.keys())

    def test_request_span_tags_structure(self) -> None:
        """Request span tags should include environment and flow.

        Expected:
            tags = [environment, flow]
            where environment in ["dev", "staging", "prod"]
            and flow in ["halachic", "search", "general", "refuse"]
        """
        from chat.observability.tracer import start_span

        with start_span(name="request", type="task") as span:
            span.log(tags=["dev"])  # First call with environment
            span.log(tags=["halachic"])  # Second call with flow

        # Tags should be merged
        assert "dev" in span.data.tags
        assert "halachic" in span.data.tags


class TestExpectedRouterSpanData:
    """Test the data structure for router spans.

    Based on router_service.py route().
    """

    def test_router_span_input_structure(self) -> None:
        """Router span input should match what route() logs.

        Expected:
            input = {
                "query": str,
                "conversation_summary": str,
                "previous_flow": str | None,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="router", type="function") as span:
            span.log(
                input={
                    "query": "What is Shabbat?",
                    "conversation_summary": "User asked about Jewish holidays.",
                    "previous_flow": "GENERAL",
                }
            )

        expected_keys = {"query", "conversation_summary", "previous_flow"}
        assert expected_keys <= set(span.data.input.keys())

    def test_router_span_output_structure(self) -> None:
        """Router span output should match what route() logs.

        Expected:
            output = {
                "flow": str,
                "confidence": float,
                "decision_id": str,
                "reason_codes": list[str],
                "tools": list[str],
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="router", type="function") as span:
            span.log(
                output={
                    "flow": "HALACHIC",
                    "confidence": 0.95,
                    "decision_id": "dec_abc123",
                    "reason_codes": ["ROUTE_HALACHIC_KEYWORDS"],
                    "tools": ["get_text", "text_search"],
                }
            )

        expected_keys = {"flow", "confidence", "decision_id", "reason_codes", "tools"}
        assert expected_keys <= set(span.data.output.keys())

    def test_router_span_metadata_structure(self) -> None:
        """Router span metadata should match what route() logs.

        Expected:
            metadata = {
                "decision_id": str,
                "session_action": str,
                "core_prompt_id": str,
                "flow_prompt_id": str,
                "classifier_type": str,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="router", type="function") as span:
            span.log(
                metadata={
                    "decision_id": "dec_abc123",
                    "session_action": "CONTINUE",
                    "core_prompt_id": "core-8fbc",
                    "flow_prompt_id": "bt_prompt_halachic",
                    "classifier_type": "ai",
                }
            )

        expected_keys = {
            "decision_id",
            "session_action",
            "core_prompt_id",
            "flow_prompt_id",
            "classifier_type",
        }
        assert expected_keys <= set(span.data.metadata.keys())


class TestExpectedAgentSpanData:
    """Test the data structure for agent spans.

    Based on claude_service.py send_message().
    """

    def test_agent_span_input_structure(self) -> None:
        """Agent span input should match what send_message logs.

        Expected:
            input = {
                "query": str,
                "messages": list[dict],
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="chat-agent", type="llm") as span:
            span.log(
                input={
                    "query": "What is Shabbat?",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "What is Shabbat?"},
                    ],
                }
            )

        assert "query" in span.data.input
        assert "messages" in span.data.input
        assert isinstance(span.data.input["messages"], list)

    def test_agent_span_output_structure(self) -> None:
        """Agent span output should match what send_message logs.

        Expected:
            output = {
                "response": str,
                "refs": list[str],
                "tool_calls": list[dict],
                "was_refused": bool,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="chat-agent", type="llm") as span:
            span.log(
                output={
                    "response": "Shabbat is the day of rest.",
                    "refs": ["Genesis 2:2", "Exodus 20:8"],
                    "tool_calls": [{"name": "get_text", "input": {"reference": "Genesis 2:2"}}],
                    "was_refused": False,
                }
            )

        expected_keys = {"response", "refs", "tool_calls", "was_refused"}
        assert expected_keys <= set(span.data.output.keys())

    def test_agent_span_metadata_structure(self) -> None:
        """Agent span metadata should match what send_message logs.

        Expected:
            metadata = {
                "model": str,
                "temperature": float,
                "max_tokens": int,
                "core_prompt_id": str,
                "core_prompt_version": str,
                "flow_prompt_id": str,
                "flow_prompt_version": str,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="chat-agent", type="llm") as span:
            span.log(
                metadata={
                    "model": "claude-sonnet-4-5-20250929",
                    "temperature": 0.7,
                    "max_tokens": 8000,
                    "core_prompt_id": "core-8fbc",
                    "core_prompt_version": "stable",
                    "flow_prompt_id": "bt_prompt_halachic",
                    "flow_prompt_version": "stable",
                }
            )

        expected_keys = {
            "model",
            "temperature",
            "max_tokens",
            "core_prompt_id",
            "core_prompt_version",
            "flow_prompt_id",
            "flow_prompt_version",
        }
        assert expected_keys <= set(span.data.metadata.keys())


class TestExpectedLLMCallSpanData:
    """Test the data structure for individual LLM call spans.

    Based on claude_service.py llm-call-N spans.
    """

    def test_llm_call_span_input_structure(self) -> None:
        """LLM call span input should match what claude_service logs.

        Expected:
            input = {
                "messages": list[dict],
                "message_count": int,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="llm-call-1", type="llm") as span:
            span.log(
                input={
                    "messages": [{"role": "user", "content": "test"}],
                    "message_count": 5,
                }
            )

        assert "messages" in span.data.input
        assert "message_count" in span.data.input

    def test_llm_call_span_output_structure(self) -> None:
        """LLM call span output should match what claude_service logs.

        Expected:
            output = {
                "text": str | None,
                "tool_calls": list[dict],
                "stop_reason": str,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="llm-call-1", type="llm") as span:
            span.log(
                output={
                    "text": "Here is the answer...",
                    "tool_calls": [{"name": "get_text", "input": {"reference": "Genesis 1:1"}}],
                    "stop_reason": "end_turn",
                }
            )

        expected_keys = {"text", "tool_calls", "stop_reason"}
        assert expected_keys <= set(span.data.output.keys())

    def test_llm_call_span_metrics_structure(self) -> None:
        """LLM call span metrics should include latency and tokens.

        Expected:
            metrics = {
                "latency_ms": int,
                "prompt_tokens": int,
                "completion_tokens": int,
                ...
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="llm-call-1", type="llm") as span:
            span.log(
                metrics={
                    "latency_ms": 500,
                    "prompt_tokens": 200,
                    "completion_tokens": 100,
                }
            )

        assert "latency_ms" in span.data.metrics
        assert "prompt_tokens" in span.data.metrics
        assert "completion_tokens" in span.data.metrics


class TestExpectedToolSpanData:
    """Test the data structure for tool execution spans.

    Based on claude_service.py tool:* spans.
    """

    def test_tool_span_input_is_json_string(self) -> None:
        """Tool span input should be JSON string of tool input."""
        import json

        from chat.observability.tracer import start_span

        tool_input = {"reference": "Genesis 1:1"}
        with start_span(name="tool:get_text", type="tool") as span:
            span.log(input=json.dumps(tool_input))

        # Input is stored as-is (string)
        assert span.data.input == '{"reference": "Genesis 1:1"}'

    def test_tool_span_output_is_preview_string(self) -> None:
        """Tool span output should be preview string."""
        from chat.observability.tracer import start_span

        with start_span(name="tool:get_text", type="tool") as span:
            span.log(output="In the beginning God created...")

        assert isinstance(span.data.output, str)

    def test_tool_span_metadata_structure(self) -> None:
        """Tool span metadata should include tool info.

        Expected:
            metadata = {
                "tool_name": str,
                "tool_use_id": str,
                "is_error": bool,
            }
        """
        from chat.observability.tracer import start_span

        with start_span(name="tool:get_text", type="tool") as span:
            span.log(
                metadata={
                    "tool_name": "get_text",
                    "tool_use_id": "toolu_abc123",
                    "is_error": False,
                }
            )

        expected_keys = {"tool_name", "tool_use_id", "is_error"}
        assert expected_keys <= set(span.data.metadata.keys())

    def test_tool_span_error_field_on_failure(self) -> None:
        """Tool span should have error field when tool fails."""
        from chat.observability.tracer import start_span

        with start_span(name="tool:get_text", type="tool") as span:
            span.log(output="Error: Reference not found", error="Error: Reference not found")

        assert span.data.error == "Error: Reference not found"


# =============================================================================
# Non-Context-Manager Span Tests (for orchestrator pattern)
# =============================================================================


class TestCreateSpan:
    """Test create_span for non-context-manager usage.

    The orchestrator creates a span in prepare_turn() and ends it in views.py.
    This pattern requires spans that can be manually managed.
    """

    def test_create_span_returns_span_instance(self) -> None:
        """create_span should return a Span instance without context manager."""
        from chat.observability import create_span

        span = create_span(name="request", type="task")
        try:
            from chat.observability.tracer import Span

            assert isinstance(span, Span)
            assert span.name == "request"
            assert span.span_type == "task"
        finally:
            span.end()

    def test_create_span_sets_current_span(self) -> None:
        """create_span should set the created span as current."""
        from chat.observability import create_span, current_span

        span = create_span(name="request", type="task")
        try:
            assert current_span() is span
        finally:
            span.end()

    def test_create_span_supports_log(self) -> None:
        """Created span should support logging data."""
        from chat.observability import create_span

        span = create_span(name="request", type="task")
        try:
            span.log(input={"query": "test"})
            span.log(output={"response": "answer"})

            assert span.data.input == {"query": "test"}
            assert span.data.output == {"response": "answer"}
        finally:
            span.end()

    def test_create_span_supports_manual_end(self) -> None:
        """Created span should support manual end() call."""
        from chat.observability import create_span

        recorded_spans = []

        class TestBackend:
            def record_span(self, span_data: dict):
                recorded_spans.append(span_data)

        # For this test we need to inject a test backend
        from chat.observability import _reset_tracer

        _reset_tracer()

        span = create_span(name="request", type="task")
        span._backends.append(TestBackend())
        span.log(input={"query": "test"})
        span.end()

        assert len(recorded_spans) == 1
        assert recorded_spans[0]["input"] == {"query": "test"}

    def test_create_span_clears_current_on_end(self) -> None:
        """After end(), the span should no longer be current."""
        from chat.observability import create_span, current_span

        span = create_span(name="request", type="task")
        assert current_span() is span

        span.end()
        # After end, current span should be None (root span)
        assert current_span() is None

    def test_nested_spans_with_create_span(self) -> None:
        """Child spans should nest under create_span parent."""
        from chat.observability import create_span, start_span

        parent = create_span(name="request", type="task")
        try:
            with start_span(name="child", type="llm") as child:
                assert child.parent_id == parent.span_id
                assert child.trace_id == parent.trace_id
        finally:
            parent.end()

    def test_create_span_uses_global_tracer_backends(self) -> None:
        """create_span should use the global tracer's backends."""
        from unittest.mock import MagicMock, patch

        from chat.observability import _reset_tracer, create_span

        _reset_tracer()

        mock_bt_span = MagicMock()
        mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
        mock_bt_span.__exit__ = MagicMock(return_value=None)

        with patch("braintrust.start_span", return_value=mock_bt_span):
            with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
                _reset_tracer()  # Reset to pick up env var

                span = create_span(name="test", type="task")
                span.log(input={"query": "test"})
                span.end()

                # Should have recorded to braintrust backend
                mock_bt_span.log.assert_called()
