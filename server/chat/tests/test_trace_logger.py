"""Tests for BraintrustTraceLogger origin logging."""

from unittest.mock import MagicMock

from chat.V2.agent.contracts import MessageContext
from chat.V2.agent.trace_logger import BraintrustTraceLogger


class TestTraceLoggerOrigin:
    """Test that BraintrustTraceLogger logs origin metadata and dev tag."""

    def setup_method(self):
        self.logger = BraintrustTraceLogger()
        self.span = MagicMock()

    def test_non_prod_origin_logs_dev_tag(self):
        ctx = MessageContext(origin="local")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "local"
        assert call_kwargs["tags"] == ["dev"]

    def test_prod_origin_logs_no_tag(self):
        ctx = MessageContext(origin="sefaria-prod")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "sefaria-prod"
        assert "tags" not in call_kwargs

    def test_origin_always_in_metadata(self):
        ctx = MessageContext(origin="eval-runner")
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "eval-runner"

    def test_no_origin_logs_dev_tag(self):
        ctx = MessageContext(origin=None)
        self.logger.log_input(bt_span=self.span, user_message="hi", context=ctx, model="test")
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["tags"] == ["dev"]
