"""Contract for the beta metrics-only Collector configuration."""

import yaml

from _otelcol import ROLE, environment


def config():
    template = (ROLE / "templates/otelcol-config.yml.j2").read_text()
    return yaml.safe_load(environment().from_string(template).render())


def test_http_receiver_and_metrics_only_pipelines():
    cfg = config()
    assert cfg["receivers"] == {"otlp": {"protocols": {
        "http": {"endpoint": "${env:OTEL_HTTP_LISTEN}"}}}}
    assert cfg["service"] == {"pipelines": {
        "traces": {"receivers": ["otlp"], "processors": ["memory_limiter", "batch"],
                   "exporters": ["span_metrics", "service_graph"]},
        "metrics": {"receivers": ["span_metrics", "service_graph"],
                    "processors": ["memory_limiter", "batch"],
                    "exporters": ["prometheusremotewrite"]},
    }}
    assert set(cfg) == {"receivers", "processors", "connectors", "exporters", "service"}


def test_seconds_histograms_and_cumulative_temporality():
    connectors = config()["connectors"]
    assert set(connectors) == {"span_metrics", "service_graph"}
    span = connectors["span_metrics"]
    buckets = ["50ms", "100ms", "250ms", "500ms", "1s", "2.5s", "5s", "10s", "30s", "60s", "120s"]
    assert span["namespace"] == "traces.span.metrics"
    assert span["histogram"] == {"unit": "s", "explicit": {"buckets": buckets}}
    assert span["aggregation_temporality"] == "AGGREGATION_TEMPORALITY_CUMULATIVE"
    assert connectors["service_graph"]["latency_histogram_buckets"] == buckets
    assert "aggregation_temporality" not in connectors["service_graph"]


def test_dimensions_are_bounded_without_invented_model_defaults():
    connectors = config()["connectors"]
    span = connectors["span_metrics"]
    assert span["dimensions"] == [{"name": "gen_ai.request.model"}, {"name": "gen_ai.system"}]
    assert span["exclude_dimensions"] == ["collector.instance.id"]
    assert span["resource_metrics_key_attributes"] == ["service.name"]
    assert span["add_resource_attributes"] is False
    assert span["aggregation_cardinality_limit"] == 5000
    assert span["metrics_flush_interval"] == "15s"
    assert span["metrics_expiration"] == span["series_expiration"] == "15m"
    assert span["exemplars"] == span["events"] == {"enabled": False}
    graph = connectors["service_graph"]
    assert graph["dimensions"] == []
    assert graph["store"] == {"ttl": "5m", "max_items": 10000}
    assert graph["metrics_flush_interval"] == "15s"


def test_remote_write_translation_resource_exclusion_and_bounded_queue():
    assert config()["exporters"] == {"prometheusremotewrite": {
        "http": {"endpoint": "${env:VM_REMOTE_WRITE_URL}"},
        "translation_strategy": "UnderscoreEscapingWithSuffixes",
        "resource_constant_labels": {"excluded": ["*"]},
        "target_info": {"enabled": False}, "disable_scope_info": True,
        "remote_write_queue": {"enabled": True, "queue_size": 1000, "num_consumers": 1},
        "max_batch_request_parallelism": 1,
        "retry_on_failure": {"enabled": True, "initial_interval": "1s",
                             "max_interval": "30s", "max_elapsed_time": "5m"},
    }}
    assert config()["processors"] == {
        "memory_limiter": {"check_interval": "1s", "limit_mib": 384, "spike_limit_mib": 64},
        "batch": {"timeout": "5s", "send_batch_size": 512, "send_batch_max_size": 1024},
    }
