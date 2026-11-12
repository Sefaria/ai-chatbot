import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from chat.V2.personalization.llm import (
    AnthropicLLMClient,
    ClaudeCodeLLMClient,
    FakeLLMClient,
    LLMResult,
    UsageLedger,
    estimate_cost,
)


def cli_output(text="ok", inp=10, cache_create=0, cache_read=0, out=5, warning=None):
    data = {
        "result": text,
        "usage": {
            "input_tokens": inp,
            "cache_creation_input_tokens": cache_create,
            "cache_read_input_tokens": cache_read,
            "output_tokens": out,
        },
    }
    lines = ([warning] if warning else []) + [json.dumps(data)]
    return "\n".join(lines) + "\n"


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_estimate_cost_prefix_match():
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000) == pytest.approx(6.0)
    assert estimate_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.6)
    assert estimate_cost("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
    assert estimate_cost("claude-haiku-5-5", 0, 1_000_000) == pytest.approx(0.50)
    assert estimate_cost("claude-sonnet-4-6", 1_000_000, 0) == pytest.approx(3.0)
    assert estimate_cost("claude-sonnet-5-5", 0, 1_000_000) == pytest.approx(10.0)
    assert estimate_cost("unknown-model", 100, 100) == 0.0


def test_fake_client_counts_tokens_as_quarter_of_characters():
    client = FakeLLMClient(lambda system, user: "x" * 40)
    result = client.complete(system="s" * 80, user="u" * 40, model="claude-haiku-5-5")
    assert (result.text, result.input_tokens, result.output_tokens) == ("x" * 40, 30, 10)
    assert client.calls[0]["model"] == "claude-haiku-5-5"


def test_ledger_totals_group_by():
    ledger = UsageLedger()
    ledger.record("a", "turn", "u1", "m", LLMResult("", 10, 5, 0.5, 1))
    ledger.record("a", "batch", "u2", "m", LLMResult("", 20, 5, 0.25, 1))
    ledger.record("b", "turn", "u1", "m", LLMResult("", 1, 1, 0.01, 1))
    assert ledger.totals()["all"]["calls"] == 3
    by_strategy = ledger.totals(group_by="strategy")
    assert by_strategy["a"] == {
        "calls": 2,
        "input_tokens": 30,
        "output_tokens": 10,
        "cost_usd": 0.75,
    }
    assert by_strategy["b"]["calls"] == 1
    assert ledger.totals(group_by="phase")["batch"]["input_tokens"] == 20
    assert ledger.totals(group_by="user_id")["u1"]["calls"] == 2


def test_anthropic_client_reads_usage_from_a_mocked_response():
    response = MagicMock()
    response.content = [MagicMock(type="text", text='{"updates": []}')]
    response.usage = MagicMock(
        input_tokens=100,
        cache_creation_input_tokens=20,
        cache_read_input_tokens=30,
        output_tokens=7,
    )
    anthropic = MagicMock()
    anthropic.messages.create.return_value = response
    result = AnthropicLLMClient(client=anthropic).complete(
        system="S", user="U", model="claude-haiku-4-5", max_tokens=50
    )
    assert (result.text, result.input_tokens, result.output_tokens) == ('{"updates": []}', 150, 7)
    kwargs = anthropic.messages.create.call_args.kwargs
    assert kwargs["system"] == "S" and kwargs["messages"] == [{"role": "user", "content": "U"}]
    assert kwargs["max_tokens"] == 50 and kwargs["model"] == "claude-haiku-4-5"


@patch("chat.V2.personalization.llm.subprocess.run")
def test_claude_code_runs_headless_without_tools_and_parses_usage(run, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_CLAUDE_CONFIG_DIR", "/tmp/cfg")
    run.return_value = completed(
        cli_output("hello", inp=10, cache_create=100, cache_read=50, out=5)
    )
    result = ClaudeCodeLLMClient().complete(system="SYS", user="USR", model="claude-haiku-5-5")
    assert (result.text, result.input_tokens, result.raw_input_tokens) == ("hello", 160, 160)
    assert result.output_tokens == 5
    argv = run.call_args.args[0]
    assert argv[:3] == ["claude", "-p", "USR"]
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == 180 and kwargs.get("shell") is None
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == "/tmp/cfg"
    # Thinking stays off for Haiku 5.5 only when stdin is closed as well as the env var is set.
    assert kwargs["env"]["MAX_THINKING_TOKENS"] == "0" and kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["cwd"] != "."


@patch("chat.V2.personalization.llm.subprocess.run")
def test_claude_code_calibration_is_subtracted_per_model(run):
    client = ClaudeCodeLLMClient()
    run.return_value = completed(cli_output("x", inp=3, cache_create=4000, out=1))
    assert client.calibrate("claude-haiku-5-5") == 4003
    run.return_value = completed(cli_output("hi", inp=10, cache_create=4000, out=2))
    result = client.complete(system="s", user="u", model="claude-haiku-5-5")
    assert (result.input_tokens, result.raw_input_tokens) == (7, 4010)
    run.return_value = completed(cli_output("hi", inp=1, out=2))
    assert client.complete(system="s", user="u", model="claude-haiku-5-5").input_tokens == 0
    assert client.complete(system="s", user="u", model="other").input_tokens == 1
    assert run.call_args_list[0].args[0][2] == "x"


@patch("chat.V2.personalization.llm.subprocess.run")
def test_claude_code_ignores_warning_line_before_json(run):
    run.return_value = completed(cli_output("fine", warning="Warning: config {not json} found"))
    assert ClaudeCodeLLMClient().complete(system="s", user="u", model="m").text == "fine"


@patch("chat.V2.personalization.llm.subprocess.run")
def test_claude_code_retries_once_then_succeeds(run):
    run.side_effect = [completed("", returncode=1, stderr="boom"), completed(cli_output("ok"))]
    assert ClaudeCodeLLMClient().complete(system="s", user="u", model="m").text == "ok"
    assert run.call_count == 2


@patch("chat.V2.personalization.llm.subprocess.run")
def test_claude_code_retries_on_unparsable_output_then_raises(run):
    run.return_value = completed("not json at all")
    with pytest.raises(RuntimeError):
        ClaudeCodeLLMClient().complete(system="s", user="u", model="m")
    assert run.call_count == 2
