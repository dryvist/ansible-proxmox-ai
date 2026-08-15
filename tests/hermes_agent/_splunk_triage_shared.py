"""Shared fixtures for the Splunk triage digest self-checks.

Loads the deployed splunk-triage.py.j2 template and the cron markup guard
(extracted from its blockinfile in tasks/main.yml) ONCE, so every split test
module exercises the same rendered artifacts Ansible would actually ship —
not a hand-copied approximation of them.
"""
import datetime as dt
import logging
import re
import tempfile
import types
from pathlib import Path

from _role_files import role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-triage.py.j2"
TASKS_PATH = REPO_ROOT / "roles" / "hermes_agent"

STATE_DIR = tempfile.mkdtemp(prefix="splunk-triage-selfcheck-")
# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "ENV_PATH": str(Path(STATE_DIR) / "unused.env"),
    "STATE_PATH": str(Path(STATE_DIR) / "splunk-error-digest.json"),
    "TITLE": "Splunk error triage",
    "INDEXES": ["os"],
    "TERMS": ["error", "failed", "critical"],
    "EARLIEST": "-1h",
    "TOP_N": 12,
    "MAX_FINDINGS": 8,
    "ISSUES_MARKER": "[ISSUES]",
}


def load_triage_module(config=None):
    """Render the template's config lines to fixtures and import it as a module.

    `config` overrides the defaults so one template can be exercised as any of
    the jobs in hermes_agent_triage_jobs.
    """
    config = config or FIXTURE_CONFIG
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in config, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {config[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_triage")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


def load_markup_guard():
    """Extract the guard from its blockinfile in tasks/main.yml and import it.

    The block is indented under `block: |` in YAML; dedent it back to module
    level. Executing the shipped text (rather than a copy) is the point — a
    drifted guard fails here instead of in Slack.
    """
    lines = role_tasks_text(TASKS_PATH).splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "block: |"
                 and "def _cron_markup_guard" in "\n".join(lines[i:i + 3]))
    body = []
    indent = None
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" " * 6):
            break
        if indent is None and line.strip():
            indent = len(line) - len(line.lstrip())
        body.append(line[indent:] if line.strip() else "")
    source = "\n".join(body)
    assert "def _cron_markup_guard" in source, "guard block not found in tasks/main.yml"
    mod = types.ModuleType("cron_markup_guard")
    # Seed the exec namespace directly: the guard resolves `re` and `logger` as
    # module globals, which is a dict operation, not attribute assignment.
    mod.__dict__["re"] = re
    mod.__dict__["logger"] = logging.getLogger("selfcheck")
    exec(compile(source, str(TASKS_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod._cron_markup_guard


TRIAGE = load_triage_module()
GUARD = load_markup_guard()
JOB = {"id": "cc8872cfb71f", "name": "splunk-error-triage"}
DAY = dt.datetime(2026, 7, 24, 19, 38, tzinfo=dt.timezone.utc)


def at(hour, day=24):
    return DAY.replace(day=day, hour=hour)


def rows(spec, sourcetype="syslog", total_sigs=None):
    """Build stats rows from {signature: {host: count}}.

    Mirrors what the SPL returns: one row per (sig, host, sourcetype), each
    carrying total_sigs so the scope line can be honest about the TOP_N cap.
    """
    out = [{"sig": sig, "host": h, "sourcetype": sourcetype, "count": str(c),
            "sample": f"<30>Jul 24 19:38:00 {h} {sig}"}
           for sig, hosts in spec.items() for h, c in hosts.items()]
    if total_sigs is not None:
        for row in out:
            row["total_sigs"] = str(total_sigs)
    return out


def mixed(spec, total_sigs=None):
    """Rows from {signature: {host: {sourcetype: count}}} — for the mix tier."""
    out = [{"sig": sig, "host": h, "sourcetype": st, "count": str(c), "sample": sig}
           for sig, hosts in spec.items()
           for h, sts in hosts.items() for st, c in sts.items()]
    if total_sigs is not None:
        for row in out:
            row["total_sigs"] = str(total_sigs)
    return out


# Two real signatures, verbatim in shape from the live estate (2026-07-28).
RAFT = ('bao[<pid>]: <ts> [ERROR] storage.raft: failed to heartbeat to: '
        'peer=<ip>:<n> error="dial tcp <ip>:<n>: connect: no route to host"')
TMPMOUNT = "systemd[<pid>]: Failed to mount tmp.mount - Temporary Directory /tmp."
OTLP = ("open-webui[<pid>]: <ts> | ERROR | opentelemetry.exporter.otlp.proto.http."
        "trace_exporter:export:<n> - Failed to export span batch")


# Indexes that exist in the homelab Splunk, from
# `| eventcount summarize=false index=*`, which ENUMERATES indexes. Do not
# derive this from `tstats`: that only returns indexes with data in the search
# window, so it silently omits real-but-idle ones and would make this check
# reject valid config. A job searching an index that does not exist returns
# nothing for that part of its search and looks entirely healthy doing it —
# which is what `index=network` did in the prompts these jobs replace. Refresh
# this list when an index is genuinely added; do not add a name to make a test
# pass.
KNOWN_INDEXES = {
    "ai", "claude", "codex", "dns", "firewall", "gemini", "genai_traces",
    "hermes", "history", "honeypot", "host_metrics", "llm", "llm_metrics",
    "mac_perf", "main", "netflow", "netmon_metrics", "network", "openai",
    "openbao_audit", "os", "os_metrics", "otel", "proxy", "summary", "unifi",
    "unifi_metrics", "vscode",
}
