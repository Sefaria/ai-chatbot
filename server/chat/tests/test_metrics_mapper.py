from chat.V2.agent.metrics_mapper import UsageMetrics, build_braintrust_metrics


def test_build_braintrust_metrics_includes_time_to_first_final_response_token_when_present():
    metrics = build_braintrust_metrics(
        latency_ms=1200,
        tool_count=2,
        llm_call_count=3,
        usage=UsageMetrics(input_tokens=10, output_tokens=5),
        total_cost_usd=0.01,
        time_to_first_final_response_token=0.8,
    )

    assert metrics["time_to_first_final_response_token"] == 0.8


def test_build_braintrust_metrics_omits_time_to_first_final_response_token_when_absent():
    metrics = build_braintrust_metrics(
        latency_ms=1200,
        tool_count=2,
        llm_call_count=3,
        usage=UsageMetrics(),
        total_cost_usd=None,
    )

    assert "time_to_first_final_response_token" not in metrics
