import json

from chat.V2.file_trace import AgentFileTracer


def test_agent_file_tracer_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_FILE_TRACE_ENABLED", raising=False)

    assert AgentFileTracer.create(session_id="sess", turn_id="turn") is None


def test_agent_file_tracer_writes_jsonl_and_redacts_sensitive_values(tmp_path, monkeypatch):
    path = tmp_path / "agent-debug.jsonl"
    monkeypatch.setenv("AGENT_FILE_TRACE_ENABLED", "1")
    monkeypatch.setenv("AGENT_FILE_TRACE_PATH", str(path))
    monkeypatch.setenv("AGENT_FILE_TRACE_RUN_ID", "run-test")

    tracer = AgentFileTracer.create(session_id="sess-1", turn_id="turn-1")
    assert tracer is not None

    tracer.emit(
        "sample_event",
        {
            "message": "hello",
            "api_key": "secret-api-key",
            "nested": {"encrypted_user_token": "secret-token"},
        },
    )

    event = json.loads(path.read_text(encoding="utf-8").strip())
    assert event["run_id"] == "run-test"
    assert event["trace_id"] == "turn-1"
    assert event["session_id"] == "sess-1"
    assert event["turn_id"] == "turn-1"
    assert event["event_type"] == "sample_event"
    assert event["payload"]["message"] == "hello"
    assert event["payload"]["api_key"] == "[redacted]"
    assert event["payload"]["nested"]["encrypted_user_token"] == "[redacted]"
