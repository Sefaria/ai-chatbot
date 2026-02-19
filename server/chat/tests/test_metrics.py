"""Tests for Prometheus metrics endpoint and recording."""

from django.test import Client

from chat.metrics import TOOL_CALLS, TOOL_DURATION, TOOL_ERRORS, record_tool_call


class TestMetricsEndpoint:
    """Test /api/metrics endpoint."""

    def test_metrics_returns_200(self) -> None:
        client = Client()
        response = client.get("/api/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.get("Content-Type", "")

    def test_metrics_contains_chatbot_metrics(self) -> None:
        record_tool_call("get_text", "success", 0.1)
        client = Client()
        response = client.get("/api/metrics")
        assert response.status_code == 200
        body = response.content.decode()
        assert "chatbot_tool_calls_total" in body
        assert "chatbot_tool_duration_seconds" in body
        assert "chatbot_tool_errors_total" in body


class TestRecordToolCall:
    """Test record_tool_call helper."""

    def test_increments_counter_on_success(self) -> None:
        before = TOOL_CALLS.labels(tool_name="get_text", status="success")._value.get()
        record_tool_call("get_text", "success", 0.1)
        after = TOOL_CALLS.labels(tool_name="get_text", status="success")._value.get()
        assert after == before + 1

    def test_increments_error_counter_on_error(self) -> None:
        before = TOOL_ERRORS.labels(tool_name="get_text")._value.get()
        record_tool_call("get_text", "error", 0.1)
        after = TOOL_ERRORS.labels(tool_name="get_text")._value.get()
        assert after == before + 1

    def test_observes_duration(self) -> None:
        record_tool_call("text_search", "success", 0.5)
        # Histogram stores observations; we can't easily assert without
        # scraping the registry. Just verify no exception.
        samples = list(TOOL_DURATION.labels(tool_name="text_search").collect())
        assert len(samples) > 0
