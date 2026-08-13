"""Shared fixtures for the hourly Splunk digest self-checks.

The digest script is a Jinja template whose STDOUT goes verbatim to Slack, so
the contract under test is the *emitted text*. Loads the deployed
splunk-digest.py.j2 template ONCE, so every split test module exercises the
same rendered artifact Ansible would actually ship.
"""
import datetime as dt
import re
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-digest.py.j2"
TEMPLATE = TEMPLATE_PATH.read_text()

STATE_DIR = tempfile.mkdtemp(prefix="splunk-digest-selfcheck-")
# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "ENV_PATH": str(Path(STATE_DIR) / "unused.env"),
    "STATE_PATH": str(Path(STATE_DIR) / "splunk-digest.json"),
    "EXPECTED_CONTINUOUS": ["os", "network", "firewall"],
    "STALENESS_MIN": 60,
    "EARLIEST": "-24h",
    "ISSUES_MARKER": "[ISSUES]",
}


def load_digest_module():
    """Render the template's config lines to fixtures and import it as a module."""
    out = []
    for line in TEMPLATE.splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in FIXTURE_CONFIG, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {FIXTURE_CONFIG[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_digest")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


DIGEST = load_digest_module()


def ledger_keys(state):
    """The finding keys a state file has ledgered, read through the script's own
    accessor rather than off the raw record shape.

    The ledger stores {"k": key, "t": text} records so the heartbeat can restate
    what is still holding instead of only counting it. Assertions that reached
    into ["ledger"]["keys"] directly were pinning that layout, not the contract,
    and broke the moment text was added alongside the key."""
    day = (state or {}).get("ledger", {}).get("day")
    return DIGEST.load_ledger(state, day)


DAY = dt.datetime(2026, 7, 24, 0, 52, tzinfo=dt.timezone.utc)


def at(hour, day=24):
    return DAY.replace(day=day, hour=hour)


def rows(spec, ts):
    """Build tstats rows from {index: {host: {sourcetype: volume}}}.

    Values are strings, exactly as Splunk returns them.
    """
    return [
        {"index": idx, "host": host, "sourcetype": stype,
         "vol": str(vol), "last_time": str(ts)}
        for idx, hosts in spec.items()
        for host, stypes in hosts.items()
        for stype, vol in stypes.items()
    ]


BASE = {
    "os": {"host-a": {"syslog": 1_000_000}, "host-b": {"syslog": 200_000}},
    "network": {"host-c": {"ipfix": 400_000}},
    "firewall": {"host-d": {"pan:traffic": 50_000}},
}


def scale(spec, index, factor):
    """Same shape, one index's volumes multiplied — a real, computable move."""
    out = {i: {h: dict(s) for h, s in hosts.items()} for i, hosts in spec.items()}
    for host in out[index]:
        for stype in out[index][host]:
            out[index][host][stype] = int(out[index][host][stype] * factor)
    return out


def with_stale_host(hour, host, hours):
    """BASE rows for `hour`, with one host's newest event pushed `hours` into the past."""
    now = int(at(hour).timestamp())
    out = rows(BASE, now - 60)
    for row in out:
        if row["host"] == host:
            row["last_time"] = str(now - hours * 3600)
    return out


def step(spec, state, when):
    """One hourly run. `spec` is a nested dict or a raw row list."""
    now = when if isinstance(when, dt.datetime) else at(when)
    results = rows(spec, int(now.timestamp()) - 60) if isinstance(spec, dict) else spec
    return DIGEST.build_digest(results, now, state)
