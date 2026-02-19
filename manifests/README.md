# Kubernetes Manifests

## ServiceMonitor

The `servicemonitor.yaml` configures Prometheus Operator to scrape metrics from the ai-chatbot service.

### Requirements

1. **Prometheus Operator** must be installed in the cluster.
2. **Target Service** must have:
   - Label `app.kubernetes.io/name: ai-chatbot` (or update the ServiceMonitor selector)
   - A port named `http` (or update `spec.endpoints[].port` in the ServiceMonitor)
3. **Prometheus** must be configured to select this ServiceMonitor (e.g. `serviceMonitorSelector` matching the ServiceMonitor labels).

### Service port example

If your Service does not have a port named `http`, add it:

```yaml
spec:
  ports:
    - name: http
      port: 8080
      targetPort: 8080
```

### Metrics exposed

- `chatbot_tool_calls_total` — Tool invocations (labels: tool_name, status)
- `chatbot_tool_duration_seconds` — Tool latency histogram (labels: tool_name)
- `chatbot_tool_errors_total` — Tool errors (labels: tool_name)

See `docs/plans/metrics-for-chatbot-agent-tools.md` for Grafana query examples.
