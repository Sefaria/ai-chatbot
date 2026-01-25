"""Tests for tracing backends.

Tests for:
1. BraintrustBackend - wraps braintrust SDK
2. DatabaseBackend - logs to our database (future)
"""

from unittest.mock import MagicMock, patch


class TestBraintrustBackend:
    """Test the Braintrust backend that wraps the braintrust SDK."""

    def test_backend_creates_braintrust_span(self) -> None:
        """Backend should create a braintrust span with correct name and type."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span) as mock_start:
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.record_span(
                {
                    "name": "request",
                    "type": "task",
                    "input": {"query": "test"},
                    "output": {"response": "answer"},
                    "metadata": {},
                    "metrics": {},
                    "tags": [],
                    "error": None,
                }
            )

            mock_start.assert_called_once_with(name="request", type="task")

    def test_backend_logs_input_to_braintrust(self) -> None:
        """Backend should log input data to braintrust span."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.record_span(
                {
                    "name": "request",
                    "type": "task",
                    "input": {"query": "What is Shabbat?"},
                    "output": None,
                    "metadata": {"session_id": "abc"},
                    "metrics": {},
                    "tags": ["dev"],
                    "error": None,
                }
            )

            # Check that log was called with input
            mock_bt_span.log.assert_called()
            call_kwargs = mock_bt_span.log.call_args[1]
            assert call_kwargs.get("input") == {"query": "What is Shabbat?"}

    def test_backend_logs_output_and_metrics(self) -> None:
        """Backend should log output and metrics to braintrust span."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.record_span(
                {
                    "name": "request",
                    "type": "task",
                    "input": {"query": "test"},
                    "output": {"response": "answer"},
                    "metadata": {},
                    "metrics": {"latency_ms": 100, "tokens": 50},
                    "tags": ["halachic"],
                    "error": None,
                }
            )

            call_kwargs = mock_bt_span.log.call_args[1]
            assert call_kwargs.get("output") == {"response": "answer"}
            assert call_kwargs.get("metrics") == {"latency_ms": 100, "tokens": 50}

    def test_backend_logs_tags(self) -> None:
        """Backend should log tags to braintrust span."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.record_span(
                {
                    "name": "request",
                    "type": "task",
                    "input": None,
                    "output": None,
                    "metadata": {},
                    "metrics": {},
                    "tags": ["dev", "halachic"],
                    "error": None,
                }
            )

            call_kwargs = mock_bt_span.log.call_args[1]
            assert call_kwargs.get("tags") == ["dev", "halachic"]

    def test_backend_logs_error(self) -> None:
        """Backend should log error to braintrust span."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.record_span(
                {
                    "name": "request",
                    "type": "task",
                    "input": None,
                    "output": {"error": "Connection failed"},
                    "metadata": {},
                    "metrics": {},
                    "tags": [],
                    "error": "Connection failed",
                }
            )

            call_kwargs = mock_bt_span.log.call_args[1]
            assert call_kwargs.get("error") == "Connection failed"

    def test_backend_logs_metadata(self) -> None:
        """Backend should log metadata to braintrust span."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.record_span(
                {
                    "name": "request",
                    "type": "task",
                    "input": None,
                    "output": None,
                    "metadata": {"session_id": "abc", "user_id": "user1"},
                    "metrics": {},
                    "tags": [],
                    "error": None,
                }
            )

            call_kwargs = mock_bt_span.log.call_args[1]
            assert call_kwargs.get("metadata") == {"session_id": "abc", "user_id": "user1"}

    def test_backend_handles_none_values(self) -> None:
        """Backend should handle None values gracefully."""
        from chat.observability.backends import BraintrustBackend

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            # Should not raise
            backend.record_span(
                {
                    "name": "test",
                    "type": "task",
                    "input": None,
                    "output": None,
                    "metadata": {},
                    "metrics": {},
                    "tags": [],
                    "error": None,
                }
            )

    def test_backend_disabled_when_no_api_key(self) -> None:
        """Backend should be disabled when BRAINTRUST_API_KEY is not set."""
        from chat.observability.backends import BraintrustBackend

        with patch.dict("os.environ", {}, clear=True):
            with patch.object(BraintrustBackend, "_has_api_key", return_value=False):
                backend = BraintrustBackend()
                assert not backend.enabled

    def test_backend_enabled_when_api_key_set(self) -> None:
        """Backend should be enabled when BRAINTRUST_API_KEY is set."""
        from chat.observability.backends import BraintrustBackend

        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
            backend = BraintrustBackend()
            assert backend.enabled

    def test_disabled_backend_skips_recording(self) -> None:
        """Disabled backend should skip recording without error."""
        from chat.observability.backends import BraintrustBackend

        with patch("braintrust.start_span") as mock_start:
            backend = BraintrustBackend()
            backend.enabled = False

            backend.record_span(
                {
                    "name": "test",
                    "type": "task",
                    "input": None,
                    "output": None,
                    "metadata": {},
                    "metrics": {},
                    "tags": [],
                    "error": None,
                }
            )

            mock_start.assert_not_called()


class TestBraintrustBackendIntegration:
    """Integration tests with our Tracer class."""

    def test_tracer_with_braintrust_backend(self) -> None:
        """Tracer should work with BraintrustBackend."""
        from chat.observability.backends import BraintrustBackend
        from chat.observability.tracer import Tracer

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            backend = BraintrustBackend()
            backend.enabled = True
            tracer = Tracer(backends=[backend])

            with tracer.start_span(name="request", type="task") as span:
                span.log(input={"query": "test"})
                span.log(output={"response": "answer"})

            # Verify braintrust was called
            mock_bt_span.log.assert_called()


class TestGetTracer:
    """Test the get_tracer() function for global tracer access."""

    def test_get_tracer_returns_tracer_instance(self) -> None:
        """get_tracer() should return a Tracer instance."""
        from chat.observability import get_tracer
        from chat.observability.tracer import Tracer

        tracer = get_tracer()
        assert isinstance(tracer, Tracer)

    def test_get_tracer_returns_same_instance(self) -> None:
        """get_tracer() should return the same singleton instance."""
        from chat.observability import get_tracer

        tracer1 = get_tracer()
        tracer2 = get_tracer()
        assert tracer1 is tracer2

    def test_get_tracer_has_braintrust_backend(self) -> None:
        """get_tracer() should include BraintrustBackend."""
        from chat.observability import get_tracer
        from chat.observability.backends import BraintrustBackend

        tracer = get_tracer()
        backend_types = [type(b) for b in tracer.backends]
        assert BraintrustBackend in backend_types

    def test_reset_tracer_clears_singleton(self) -> None:
        """_reset_tracer() should clear the singleton for testing."""
        from chat.observability import _reset_tracer, get_tracer

        tracer1 = get_tracer()
        _reset_tracer()
        tracer2 = get_tracer()
        assert tracer1 is not tracer2


class TestModuleLevelFunctions:
    """Test module-level convenience functions that use the global tracer."""

    def test_module_start_span_uses_global_tracer(self) -> None:
        """Module-level start_span should use the global tracer's backends."""
        from chat.observability import _reset_tracer, start_span

        _reset_tracer()

        mock_bt_span = MagicMock()
        with patch("braintrust.start_span", return_value=mock_bt_span):
            mock_bt_span.__enter__ = MagicMock(return_value=mock_bt_span)
            mock_bt_span.__exit__ = MagicMock(return_value=None)

            with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
                _reset_tracer()  # Reset to pick up env var

                with start_span(name="test", type="task") as span:
                    span.log(input={"query": "test"})

                # The span should have recorded to braintrust
                mock_bt_span.log.assert_called()
