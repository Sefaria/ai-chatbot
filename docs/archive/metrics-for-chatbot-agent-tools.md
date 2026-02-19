# Metrics for Chatbot Agent/Tools Usage

**Story:** [SC-41316](https://app.shortcut.com/sefaria/story/41316) — MCP: add per-tool time-series metrics  
**Epic:** DevOps  
**Date:** 2026-02-19

## Executive Summary

SC-41316 asks for Prometheus metrics that support **time-series queries** (e.g. "calls per tool per hour") for the chatbot's MCP tools. This document brainstorms a comprehensive metrics strategy to capture all information needed from agent/tool usage.

---

## 1. Story Context (SC-41316)

### Current State (from story)

The story references these Prometheus metrics in `main.py`:

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `mcp_tool_calls_total` | Counter | tool_name, status | Total tool invocations |
| `mcp_tool_duration_seconds` | Histogram | tool_name | Latency per tool |
| `mcp_tool_payload_bytes` | Histogram | tool_name | Payload size |
| `mcp_active_connections` | Gauge | — | Active MCP connections |
| `mcp_errors_total` | Counter | tool_name, error_type | Error counts |

**Note:** The ai-chatbot codebase does not currently expose these Prometheus metrics. They may live in a separate MCP server or Sefaria main repo. The chatbot uses Braintrust for tracing and DB for persistence.

### Problem (from story)

> We want to map usage per `tool_name` over time, but we currently only store total calls. We cannot get time-series (e.g. "calls per tool per hour") from the existing metrics alone.

**Clarification:** Prometheus Counters with labels *are* inherently time-series. `rate(mcp_tool_calls_total[1h])` or `increase(mcp_tool_calls_total[1h])` should yield calls per second or per hour. The issue may be:

1. Metrics are not exposed at all in the chatbot service
2. Metrics are aggregated without `tool_name` label (losing per-tool breakdown)
3. Scraping interval or retention prevents useful queries

### Goal (from story)

> Add to `_run_with_metrics` (or another appropriate place) an option to send/record metrics per individual tool in a way that supports time-series queries (e.g. so we can graph usage per tool_name by time in Prometheus/Grafana).

---

## 2. Current ai-chatbot Observability

### What exists today

| Source | Data | Time-series? |
|--------|------|--------------|
| **Braintrust** | Spans, tool_calls in metadata, latency_ms, tool_count, tokens, cost | Yes (via Braintrust UI) |
| **ChatMessage** | tool_calls_data (JSON), tool_calls_count, latency_ms | Yes (via DB queries) |
| **ChatSession** | total_tool_calls, total_input_tokens, total_output_tokens, total_cost_usd | Aggregates only |
| **claude_service** | tool_calls_list per turn (tool_name, tool_input, tool_output, is_error, latency_ms) | Per-turn only |

### Gap

- No Prometheus metrics in the chatbot service
- No real-time dashboards for ops (Grafana)
- No alerting on tool error rates or latency
- Per-tool breakdown requires DB queries or Braintrust drill-down

---

## 3. Metrics to Add (Brainstorm)

### 3.1 Per-tool time-series (SC-41316 core)

| Metric | Type | Labels | Use case |
|--------|------|--------|----------|
| `chatbot_tool_calls_total` | Counter | tool_name, status (success/error) | `rate(...[1h])` → calls/sec per tool; `increase(...[1h])` → calls per hour |
| `chatbot_tool_duration_seconds` | Histogram | tool_name | p50, p95, p99 latency per tool; SLO monitoring |
| `chatbot_tool_errors_total` | Counter | tool_name, error_type (optional) | Error rate per tool; alert on spike |

**Implementation point:** In `_build_sdk_tools` handler (claude_service.py ~line 546), after `tool_executor.execute()` and before appending to `tool_calls_list`, increment/observe Prometheus metrics.

### 3.2 Agent/turn-level metrics

| Metric | Type | Labels | Use case |
|--------|------|--------|----------|
| `chatbot_turns_total` | Counter | status (success/error/guardrail_blocked) | Turn volume over time |
| `chatbot_turn_latency_seconds` | Histogram | — | End-to-end latency distribution |
| `chatbot_turn_tool_count` | Histogram | — | Tools per turn (distribution) |
| `chatbot_guardrail_blocks_total` | Counter | reason (optional) | Guardrail effectiveness |

### 3.3 Token and cost metrics

| Metric | Type | Labels | Use case |
|--------|------|--------|----------|
| `chatbot_tokens_total` | Counter | type (prompt/completion/cache_read/cache_creation) | Token usage trends |
| `chatbot_cost_usd_total` | Counter | — | Cost tracking; budget alerts |
| `chatbot_llm_calls_total` | Counter | — | Multi-step reasoning frequency |

### 3.4 Session and user metrics (optional)

| Metric | Type | Labels | Use case |
|--------|------|--------|----------|
| `chatbot_sessions_active` | Gauge | — | Concurrent sessions |
| `chatbot_messages_total` | Counter | role (user/assistant) | Message volume |

### 3.5 Tool-specific business metrics (optional)

| Metric | Type | Labels | Use case |
|--------|------|--------|----------|
| `chatbot_tool_get_text_refs_total` | Counter | — | Popular references (if we add ref as label, watch cardinality) |
| `chatbot_tool_search_queries_total` | Counter | tool_name | Search vs semantic vs in-book usage |

**Cardinality warning:** Avoid high-cardinality labels (e.g. full query text, user_id) in Prometheus. Use exemplars or logs for drill-down.

---

## 4. Implementation Options

### Option A: Add Prometheus to ai-chatbot (recommended)

- Add `prometheus_client` to the Django app
- Expose `/metrics` endpoint (or use `django-prometheus`)
- Instrument in `claude_service._build_sdk_tools` handler and `TurnLoggingService`
- **Pros:** Single source of truth, aligns with story, Grafana-ready
- **Cons:** New dependency, need to wire into existing deployment

### Option B: Rely on Braintrust + DB

- Use Braintrust for traces and ad-hoc analysis
- Add DB views or scheduled jobs to aggregate tool_calls_data into time-series
- **Pros:** No new infra
- **Cons:** Not real-time, no alerting, heavier DB load for analytics

### Option C: Hybrid

- Prometheus for RED metrics (rate, errors, duration) and alerting
- Braintrust for deep traces and token/cost
- DB for audit and long-term analytics

---

## 5. Minimal Set for SC-41316

To satisfy the story with minimal scope:

1. **`chatbot_tool_calls_total`** (Counter, labels: tool_name, status)  
   - Increment in tool handler on every call (success or error)
   - Enables: `rate(chatbot_tool_calls_total{tool_name="get_text"}[1h])` → calls/sec

2. **`chatbot_tool_duration_seconds`** (Histogram, labels: tool_name)  
   - Observe latency in tool handler
   - Enables: `histogram_quantile(0.95, rate(chatbot_tool_duration_seconds_bucket[5m]))` → p95

3. **`chatbot_tool_errors_total`** (Counter, labels: tool_name)  
   - Increment when `result.is_error`  
   - Or fold into `chatbot_tool_calls_total` with status=error

**Instrumentation point:** `server/chat/V2/agent/claude_service.py`, inside the `handler` in `_build_sdk_tools`, after `result = await self.tool_executor.execute(...)`.

---

## 6. Grafana Dashboard Ideas

| Panel | Query | Purpose |
|-------|-------|---------|
| Tool calls per hour | `sum by (tool_name) (increase(chatbot_tool_calls_total[1h]))` | Usage trends per tool |
| Tool error rate | `sum(rate(chatbot_tool_calls_total{status="error"}[5m])) / sum(rate(chatbot_tool_calls_total[5m]))` | Error ratio |
| Tool p95 latency | `histogram_quantile(0.95, sum by (le, tool_name) (rate(chatbot_tool_duration_seconds_bucket[5m])))` | Latency SLO |
| Top tools by volume | `topk(10, sum by (tool_name) (increase(chatbot_tool_calls_total[24h])))` | Most-used tools |

---

## 7. Trade-offs

| Approach | Complexity | Real-time | Alerting | Cost tracking |
|----------|------------|-----------|----------|---------------|
| Prometheus only | Medium | Yes | Yes | Via counter |
| Braintrust only | Low | Yes (UI) | Limited | Yes |
| DB aggregation | High | No | No | Yes |
| Hybrid (Prometheus + Braintrust) | Medium | Yes | Yes | Yes |

---

## 8. Recommendations

1. **Implement Option A** — Add Prometheus metrics to ai-chatbot for per-tool time-series (SC-41316).
2. **Start with the minimal set** — `chatbot_tool_calls_total`, `chatbot_tool_duration_seconds`, and optionally `chatbot_tool_errors_total`.
3. **Instrument in the tool handler** — Single place, low overhead, captures every invocation.
4. **Add agent-level metrics later** — `chatbot_turns_total`, `chatbot_turn_latency_seconds` for full visibility.
5. **Keep Braintrust** — For traces, token/cost detail, and debugging; Prometheus for ops dashboards and alerting.

---

## 9. Next Steps

- [x] Add `prometheus_client` (or `django-prometheus`) to `requirements.txt`
- [x] Create metrics module (e.g. `server/chat/metrics.py`) with Counter/Histogram definitions
- [x] Instrument tool handler in `claude_service._build_sdk_tools`
- [x] Expose `/metrics` endpoint
- [x] Add ServiceMonitor in `manifests/servicemonitor.yaml` for Prometheus Operator
- [ ] Add Grafana dashboard for tool usage
- [ ] Document Prometheus scrape config for deployment (see `manifests/README.md`)
