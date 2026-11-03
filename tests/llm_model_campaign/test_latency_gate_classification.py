"""Exercise the campaign latency gate's Jinja classification expressions."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from jinja2 import Environment
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "playbooks/tasks"
COUNTS = TASKS / "llm-model-campaign-latency-gate-counts.yml"
CLASSIFY = TASKS / "llm-model-campaign-latency-gate-classify-response.yml"
REPORT = TASKS / "llm-model-campaign-latency-gate-report.yml"


def _named(path: Path, name: str) -> dict[str, Any]:
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _render(expression: str, **context: Any) -> str:
    env = Environment()
    env.filters["to_json"] = json.dumps  # Ansible filter used by the report message
    env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None  # Ansible test
    return env.from_string(expression).render(**context).strip()


def _count_reason(
    counts: list[dict[str, Any]], selected: dict[str, Any] | None
) -> str:
    expression = _named(COUNTS, "Classify the interactive request count coverage")[
        "ansible.builtin.set_fact"
    ]["_llm_campaign_latency_reason"]
    largest = max(counts, key=lambda count: count["window_seconds"])
    return _render(
        expression,
        _llm_campaign_latency_window_response_reason="ready",
        _llm_campaign_latency_window_counts=counts,
        _llm_campaign_latency_selected_window=selected,
        _llm_campaign_latency_largest_window=largest,
        llm_campaign_latency_min_samples=20,
    )


def _scalar_reason(kind: str, count: int | None, value: str | None) -> str:
    expression = _named(CLASSIFY, "Classify the interactive Prometheus scalar response")[
        "ansible.builtin.set_fact"
    ]["_llm_campaign_latency_classification_reason"]
    return _render(
        expression,
        _llm_campaign_latency_classification_kind=kind,
        _llm_campaign_latency_classification_observation={
            "http_status": 200,
            "prometheus_status": "success",
            "result_count": count,
            "raw_value": value,
            "well_formed": True,
        },
        llm_campaign_latency_max_ratio=1.2,
    )


def test_idle_count_ladder_passes_and_reports_its_largest_window():
    counts = [
        {"window": window, "window_seconds": seconds, "sample_count": 0}
        for window, seconds in (("30m", 1800), ("2h", 7200), ("6h", 21600))
    ]
    reason = _count_reason(counts, selected=None)
    allowed = _named(REPORT, "Fail closed on interactive latency gate failures")[
        "ansible.builtin.assert"
    ]["that"][0]
    assert reason == "idle: nothing to protect"
    assert _render("{{ " + allowed + " }}", _llm_campaign_latency_reason=reason) == "True"

    idle = _named(REPORT, "Report idle interactive latency gate")
    assert idle["when"] == "_llm_campaign_latency_reason == 'idle: nothing to protect'"
    message = _render(
        idle["ansible.builtin.debug"]["msg"],
        _llm_campaign_latency_observation={
            "largest_window": "6h",
            "largest_window_count": 0,
        },
    )
    assert message.startswith("idle: nothing to protect")
    assert "largest window=6h" in message
    assert "count=0" in message


def test_empty_count_ladder_does_not_pass_as_idle():
    counts = [
        {"window": window, "window_seconds": seconds, "sample_count": None}
        for window, seconds in (("30m", 1800), ("2h", 7200), ("6h", 21600))
    ]
    assert _count_reason(counts, selected=None) == "no data: minimum sample count not met"


def test_zero_counts_in_any_numeric_spelling_pass_as_idle():
    for zero in ("0e0", "+0", ".0", "0.000", "00E+5"):
        counts = [
            {"window": window, "window_seconds": seconds, "sample_count": zero}
            for window, seconds in (("30m", 1800), ("2h", 7200), ("6h", 21600))
        ]
        assert _count_reason(counts, selected=None) == "idle: nothing to protect", zero


def test_underflowed_positive_counts_are_not_idle():
    counts = [
        {"window": window, "window_seconds": seconds, "sample_count": "1e-9999"}
        for window, seconds in (("30m", 1800), ("2h", 7200), ("6h", 21600))
    ]
    reason = _count_reason(counts, selected=None)
    assert reason == "thin: minimum sample count not met"


def test_thin_count_ladder_fails_closed_with_largest_window_and_count():
    counts = [
        {"window": window, "window_seconds": seconds, "sample_count": sample_count}
        for window, seconds, sample_count in (
            ("30m", 1800, 3),
            ("2h", 7200, 7),
            ("6h", 21600, 12),
        )
    ]
    reason = _count_reason(counts, selected=None)
    guard = _named(REPORT, "Fail closed on interactive latency gate failures")[
        "ansible.builtin.assert"
    ]
    assert reason.startswith("thin")
    allowed = guard["that"][0]
    assert _render("{{ " + allowed + " }}", _llm_campaign_latency_reason=reason) == "False"
    message = _render(
        guard["fail_msg"],
        _llm_campaign_latency_reason=reason,
        _llm_campaign_latency_observation={
            "http_status": 200,
            "prometheus_status": "success",
            "result_count": 1,
            "raw_ratio": None,
            "baseline_value": None,
            "max_ratio": 1.2,
            "window": None,
            "sample_count": 12,
            "largest_window": "6h",
            "largest_window_count": 12,
            "window_counts": [{"window": "6h", "sample_count": "12"}],
        },
    )
    assert "thin" in message
    assert "largest window=6h" in message
    assert "count=12" in message
    assert 'window counts=[{"window": "6h", "sample_count": "12"}]' in message


def test_first_sufficient_window_and_ratio_classification():
    samples = [
        {"window": window, "window_seconds": seconds, "sample_count": count}
        for window, seconds, count in (
            ("30m", 1800, 5),
            ("2h", 7200, 25),
            ("6h", 21600, 80),
        )
    ]
    selected = next(item for item in samples if item["sample_count"] >= 20)
    assert selected["window"] == "2h"
    assert _count_reason(samples, selected=selected) == "pass"
    assert _scalar_reason("ratio", count=1, value="0.7") == "pass"
    assert _scalar_reason("ratio", count=1, value="2.0") == "ratio over max"
    assert _scalar_reason("ratio", count=1, value="NaN") == "NaN"
    assert _scalar_reason("baseline", count=0, value=None) == "no data: baseline result is empty"


def _well_formed(
    timestamp: Any = 1793660000.123, sample: str = "50", metric: Any = None
) -> tuple[str, str]:
    result = [{"metric": {} if metric is None else metric, "value": [timestamp, sample]}]
    data = {"resultType": "vector", "result": result}
    counts = _named(COUNTS, "Capture interactive request counts for each latency window")["vars"][
        "_llm_campaign_latency_count_well_formed"
    ]
    scalar = _named(CLASSIFY, "Capture the interactive Prometheus scalar response")[
        "ansible.builtin.set_fact"
    ]["_llm_campaign_latency_classification_observation"]["well_formed"]
    return (
        _render(
            counts,
            _llm_campaign_latency_count_data=data,
            _llm_campaign_latency_count_result=result,
        ),
        _render(
            scalar,
            _llm_campaign_latency_response_data=data,
            _llm_campaign_latency_response_result=result,
        ),
    )


def test_sample_timestamps_must_be_finite_and_positive():
    assert _well_formed(1793660000.123) == ("True", "True")
    for bad in (float("nan"), float("inf"), float("-inf"), 0, -5, True):
        assert _well_formed(bad) == ("False", "False"), bad


def test_count_samples_must_be_finite():
    assert _well_formed(1793660000.0, "1234")[0] == "True"
    for bad in ("1e9999", "9e15", "0\n", "20\n", "-1", "-1e-9999"):
        assert _well_formed(sample=bad)[0] == "False", repr(bad)
    for zero in ("-0", "-0.0", "-0e0"):
        assert _well_formed(sample=zero)[0] == "True", zero


def test_signed_zero_counts_pass_as_idle():
    counts = [
        {"window": window, "window_seconds": seconds, "sample_count": "-0"}
        for window, seconds in (("30m", 1800), ("2h", 7200), ("6h", 21600))
    ]
    assert _count_reason(counts, selected=None) == "idle: nothing to protect"


def test_scalar_samples_must_be_finite_positive_and_untrimmed():
    for good in ("1.19", "0.5", "74.6", "NaN"):
        assert _well_formed(sample=good)[1] == "True", good
    for bad in ("1e9999", "1e-9999", "0", "-0", "1\n", " 1", "Inf", "+Inf", ""):
        assert _well_formed(sample=bad)[1] == "False", repr(bad)


def test_aggregated_results_carry_no_labels():
    for metric in ({"bad": None}, {"le": "+Inf"}, {"x": ["a"]}, {"x": True}):
        assert _well_formed(metric=metric) == ("False", "False"), metric


def test_count_stage_failure_headline_points_at_window_counts():
    expression = _named(REPORT, "Assemble interactive latency gate report values")[
        "ansible.builtin.set_fact"
    ]["_llm_campaign_latency_observation"]["http_status"]
    assert _render(expression, _llm_campaign_latency_classification_observation={}) == (
        "n/a (count stage, see window counts)"
    )
    assert _render(
        expression, _llm_campaign_latency_classification_observation={"http_status": 200}
    ) == "200"
