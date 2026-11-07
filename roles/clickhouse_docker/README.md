# ClickHouse and trace metrics

This role manages the application database, conversation tables, read-only
consumers, and a beta OpenTelemetry Collector in the existing Compose project.
The Collector derives span and service-graph metrics and remote-writes them to
the metrics store. It does not store or forward traces.

- `clickhouse_docker_otelcol_enabled` defaults to `true`. Setting it to `false`
  removes the service on converge through Compose orphan removal.
- Published receiver and metrics-store ports come from `tofu_data.constants.service_ports`.
  The metrics-store address uses its published inventory hostname and domain.
  Missing required inventory or Compose credentials fails a scoped converge.
- Collector config changes recreate only the collector service. Compose changes
  reconcile the project; the database service definition remains the same.
- The pinned image runs as its non-root user, with a 512 MiB container limit.
  The Collector's 384 MiB memory limiter, bounded queues, and cardinality limits
  are initial budgets that need workload validation.

Scoped deployment uses `playbooks/site.yml`, tag `otel_collector`, and limit
`clickhouse_group,localhost`. Converge the Collector before enabling the trace
forwarder's connection. Existing producer endpoints and trace-store legs are
managed separately.

The span connector uses cumulative seconds histograms, native service/span/status
dimensions, and optional GenAI model/system dimensions. Arbitrary resource
attributes and collector-instance identifiers are excluded. Service graphs
require original paired spans; absent instrumentation produces no graph.

Run `pytest -q tests/clickhouse_docker tests/langfuse_docker/test_clickhouse_contract.py`
for the offline contracts. Deployment acceptance still requires pinned-image
validation, Compose validation, a no-op second converge, unchanged database
container identity, real span/status/histogram samples, and exporter delivery.
Image digest verification, receiver reachability policy, and capacity checks
must precede production enablement.
