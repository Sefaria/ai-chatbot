"""
Braintrust logger for structured run logging and evals.

Logs comprehensive data for:
- Offline evaluation and regression testing
- Dataset creation for fine-tuning
- Performance analytics
"""

import os
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime
from asgiref.sync import sync_to_async
import json

from ..models import BraintrustLog, ToolCallEvent

logger = logging.getLogger('chat.logging')


@dataclass
class ToolEvent:
    """A tool call event for logging."""
    event_id: str
    tool_name: str
    tool_input: Dict[str, Any]
    tool_output: Optional[Any] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    latency_ms: Optional[int] = None
    success: bool = True
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "event_id": self.event_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error_message": self.error_message,
        }


@dataclass
class RunLog:
    """
    Complete run log for Braintrust.
    
    Captures all context needed for:
    - Evaluation
    - Dataset creation
    - Debugging
    """
    # Identifiers
    log_id: str
    session_id: str
    turn_id: str
    decision_id: str = ""
    
    # Input context
    user_message: str = ""
    conversation_summary: str = ""
    flow: str = ""
    
    # Prompt info
    core_prompt_id: str = ""
    core_prompt_version: str = ""
    flow_prompt_id: str = ""
    flow_prompt_version: str = ""
    
    # Tools
    tools_available: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    tool_events: List[ToolEvent] = field(default_factory=list)
    
    # Output
    assistant_response: str = ""
    was_refused: bool = False
    refusal_reason_codes: List[str] = field(default_factory=list)
    
    # Metrics
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    latency_ms: Optional[int] = None
    llm_calls: int = 0
    tool_calls_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Optional[float] = None
    
    # Tags
    environment: str = "dev"
    app_version: str = ""
    
    def finalize(self, response: str, was_refused: bool = False):
        """Finalize the run log with response and timing."""
        self.end_time = time.time()
        self.latency_ms = int((self.end_time - self.start_time) * 1000)
        self.assistant_response = response
        self.was_refused = was_refused
        self.tools_used = [e.tool_name for e in self.tool_events]
        self.tool_calls_count = len(self.tool_events)
    
    def to_braintrust_format(self) -> Dict[str, Any]:
        """
        Convert to Braintrust logging format.

        Returns format compatible with braintrust.log()
        """
        # Ensure all metrics are numbers (not None)
        metrics = {}
        if self.latency_ms is not None:
            metrics["latency_ms"] = self.latency_ms
        if self.llm_calls is not None:
            metrics["llm_calls"] = self.llm_calls
        if self.tool_calls_count is not None:
            metrics["tool_calls"] = self.tool_calls_count
        if self.input_tokens is not None:
            metrics["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            metrics["output_tokens"] = self.output_tokens
        if self.estimated_cost_usd is not None:
            metrics["cost_usd"] = self.estimated_cost_usd

        return {
            "id": self.log_id,
            "input": {
                "user_message": self.user_message,
                "conversation_summary": self.conversation_summary,
                "flow": self.flow,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
            },
            "output": {
                "response": self.assistant_response,
                "was_refused": self.was_refused,
                "refusal_codes": self.refusal_reason_codes,
            },
            "expected": None,  # For evals, this would be the expected output
            "metadata": {
                "decision_id": self.decision_id,
                "core_prompt_id": self.core_prompt_id,
                "core_prompt_version": self.core_prompt_version,
                "flow_prompt_id": self.flow_prompt_id,
                "flow_prompt_version": self.flow_prompt_version,
                "tools_available": self.tools_available,
                "tools_used": self.tools_used,
                "environment": self.environment,
                "app_version": self.app_version,
            },
            "metrics": metrics,
            "tags": [self.flow, self.environment],
        }


class BraintrustLogger:
    """
    Logger for sending run data to Braintrust.
    
    Features:
    - Async logging to avoid blocking
    - Local database backup
    - Batch upload support
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        project_name: Optional[str] = None,
        environment: str = "dev",
    ):
        """
        Initialize the Braintrust logger.
        
        Args:
            api_key: Braintrust API key (default: from env)
            project_name: Braintrust project name (default: from env)
            environment: Environment tag (dev, staging, prod)
        """
        self.api_key = api_key or os.environ.get('BRAINTRUST_API_KEY')
        self.project_name = project_name or os.environ.get('BRAINTRUST_PROJECT', 'sefaria-chatbot')
        self.environment = environment or os.environ.get('ENVIRONMENT', 'dev')
        
        self._braintrust = None
        self._logger = None
        self._enabled = False
        
        self._init_braintrust()
    
    def _init_braintrust(self):
        """Initialize Braintrust client."""
        if not self.api_key:
            logger.warning("⚠️  Braintrust API key not configured, logging to database only")
            return

        try:
            import braintrust
            self._braintrust = braintrust
            self._enabled = True
            logger.info(f"✅ Braintrust logging enabled for project: {self.project_name}")
        except ImportError:
            logger.warning("⚠️  braintrust package not installed, logging to database only")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Braintrust: {e}", exc_info=True)
    
    @property
    def enabled(self) -> bool:
        """Check if Braintrust logging is enabled."""
        return self._enabled
    
    def create_run_log(
        self,
        session_id: str,
        turn_id: str,
        decision_id: str = "",
        user_message: str = "",
        flow: str = "",
    ) -> RunLog:
        """Create a new run log."""
        log_id = f"log_{turn_id}"
        
        return RunLog(
            log_id=log_id,
            session_id=session_id,
            turn_id=turn_id,
            decision_id=decision_id,
            user_message=user_message,
            flow=flow,
            environment=self.environment,
        )
    
    def log_tool_event(
        self,
        run_log: RunLog,
        event_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_output: Optional[Any] = None,
        latency_ms: Optional[int] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ):
        """Add a tool event to a run log."""
        event = ToolEvent(
            event_id=event_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message,
        )
        run_log.tool_events.append(event)
    
    def log_run(self, run_log: RunLog) -> bool:
        """
        Log a completed run to Braintrust and database.

        Args:
            run_log: The completed run log

        Returns:
            True if successfully logged to Braintrust
        """
        logger.info(f"📊 Logging run: {run_log.log_id} (flow={run_log.flow}, enabled={self._enabled})")

        # Always save to database (handle async context)
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            # We're in async context, so run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(self._save_to_database, run_log)
                future.result()
        except RuntimeError:
            # Not in async context, call directly
            self._save_to_database(run_log)

        # Send to Braintrust if enabled
        if self._enabled and self._braintrust:
            try:
                return self._log_to_braintrust(run_log)
            except Exception as e:
                logger.error(f"❌ Failed to log to Braintrust: {e}", exc_info=True)
                return False
        else:
            if not self._enabled:
                logger.debug("Braintrust logging not enabled, skipping remote log")
            elif not self._braintrust:
                logger.warning("Braintrust module not initialized, skipping remote log")

        return True
    
    def _save_to_database(self, run_log: RunLog):
        """Save run log to Django database."""
        try:
            braintrust_data = run_log.to_braintrust_format()

            BraintrustLog.objects.create(
                log_id=run_log.log_id,
                session_id=run_log.session_id,
                turn_id=run_log.turn_id,
                decision_id=run_log.decision_id,
                user_message=run_log.user_message,
                conversation_summary=run_log.conversation_summary,
                flow=run_log.flow,
                core_prompt_id=run_log.core_prompt_id,
                core_prompt_version=run_log.core_prompt_version,
                flow_prompt_id=run_log.flow_prompt_id,
                flow_prompt_version=run_log.flow_prompt_version,
                tools_available=run_log.tools_available,
                tools_used=run_log.tools_used,
                assistant_response=run_log.assistant_response,
                was_refused=run_log.was_refused,
                refusal_reason_codes=run_log.refusal_reason_codes,
                latency_ms=run_log.latency_ms,
                llm_calls=run_log.llm_calls,
                tool_calls_count=run_log.tool_calls_count,
                input_tokens=run_log.input_tokens,
                output_tokens=run_log.output_tokens,
                estimated_cost_usd=run_log.estimated_cost_usd,
                environment=run_log.environment,
                app_version=run_log.app_version,
                braintrust_input=braintrust_data.get("input"),
                braintrust_output=braintrust_data.get("output"),
                braintrust_metadata=braintrust_data.get("metadata"),
            )

            # Save tool events
            for event in run_log.tool_events:
                ToolCallEvent.objects.create(
                    event_id=event.event_id,
                    session_id=run_log.session_id,
                    turn_id=run_log.turn_id,
                    decision_id=run_log.decision_id,
                    tool_name=event.tool_name,
                    tool_input=event.tool_input,
                    tool_output=event.tool_output if isinstance(event.tool_output, dict) else {"result": str(event.tool_output)[:1000]} if event.tool_output else None,
                    start_timestamp=datetime.fromtimestamp(event.start_time),
                    end_timestamp=datetime.fromtimestamp(event.end_time) if event.end_time else None,
                    latency_ms=event.latency_ms,
                    success=event.success,
                    error_message=event.error_message or "",
                    flow=run_log.flow,
                )

            logger.info(f"💾 Saved run log to database: {run_log.log_id}")

        except Exception as e:
            logger.error(f"❌ Failed to save run log to database: {e}", exc_info=True)
    
    def _log_to_braintrust(self, run_log: RunLog) -> bool:
        """Send run log to Braintrust."""
        try:
            # Initialize logger for project if needed
            if self._logger is None:
                logger.info(f"🔧 Initializing Braintrust logger for project: {self.project_name}")
                self._logger = self._braintrust.init_logger(
                    project=self.project_name,
                    api_key=self.api_key,
                )
                logger.info(f"✅ Braintrust logger initialized")

            # Log the run
            bt_data = run_log.to_braintrust_format()
            self._logger.log(
                input=bt_data["input"],
                output=bt_data["output"],
                metadata=bt_data["metadata"],
                metrics=bt_data["metrics"],
                tags=bt_data["tags"],
            )

            logger.info(f"📝 Logged run to Braintrust: {run_log.log_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Braintrust logging error: {e}", exc_info=True)
            return False
    
    def flush(self):
        """Flush any pending logs to Braintrust."""
        if self._logger:
            try:
                logger.info("🔄 Flushing Braintrust logs...")
                self._logger.flush()
                logger.info("✅ Braintrust logs flushed successfully")
            except Exception as e:
                logger.error(f"❌ Failed to flush Braintrust logs: {e}", exc_info=True)


# Default logger instance
_default_logger = None


def get_logger() -> BraintrustLogger:
    """Get or create the default Braintrust logger."""
    global _default_logger
    if _default_logger is None:
        _default_logger = BraintrustLogger()
    return _default_logger


