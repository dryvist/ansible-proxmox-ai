# Copyright (c) 2026 JacobPEvans
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
"""Pure event construction for the converge-telemetry callback.

Split from converge_telemetry.py, which now holds only the callback lifecycle.
These are ordinary functions over plain dicts with no Ansible callback state,
so they are the half worth reading (and testing) on its own when the question
is "what does a converge publish", not "when does it publish".

Not a callback plugin, which is why it does not live in callback_plugins/:
the loader inspects every *.py there and warns "skipped as invalid" on each
one without a CallbackModule class, on every run.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json

from ansible import context

STATS_KEY = "converge_telemetry"

#: Key inside that configuration carrying the playbook's own view of check mode.
CHECK_MODE_KEY = "check_mode"

SOURCETYPE_CONVERGE = "ansible:converge"
SOURCETYPE_ROSTER = "ansible:converge:roster"
SOURCETYPE_TASK = "ansible:converge:task"
SOURCETYPE_INTERRUPTED = "ansible:converge:interrupted"
SOURCETYPE_UNREACHABLE = "ansible:converge:unreachable"
SOURCE = "ansible-proxmox-apps"


def host_status(summary):
    """Return ``success`` only when Ansible itself recorded a clean host run.

    ``rescued`` counts as a failure on purpose: every ``rescue`` block in this
    repository exists solely to record an isolated play failure
    (playbooks/tasks/record_isolated_failure.yml), so a rescued host did not
    converge cleanly even though its ``failures`` counter is zero.
    """
    for counter in ("failures", "unreachable", "rescued"):
        if summary.get(counter, 0):
            return "failed"
    return "success"


def is_check_mode(config):
    """Return True when this run must not publish converge freshness.

    A check run touches nothing on the targets, so an event from one would
    refresh the freshness clock for a host that never converged — silently
    defeating the >7-day staleness alert this telemetry feeds.

    Two independent signals, either of which is sufficient:

    * ``config[CHECK_MODE_KEY]`` — the converging playbook's own
      ``ansible_check_mode``, handed over with the rest of the configuration
      via ``set_stats``. Authoritative and available however the run was
      launched.
    * ``context.CLIARGS['check']`` — the ``--check`` flag as parsed by the CLI.
      This is how ansible-core's own ``default`` callback reads check mode.
      Empty for API-driven runs (``ansible-runner`` and friends), which is why
      the ``set_stats`` signal above exists as well.
    """
    if (config or {}).get(CHECK_MODE_KEY):
        return True
    return bool((context.CLIARGS or {}).get("check"))


def _desired_state_fields(config):
    """Fields describing whether the converged-against inventory was current.

    Returns an empty dict when the verdict is absent, so a run that could not
    check publishes no claim at all. A default of ``True`` here would make the
    downstream alert silently green for every converge that never ran the check
    — the same class of silent hold the alert exists to catch.
    """
    verdict = (config or {}).get("desired_state_current")
    if verdict is None:
        return {}
    return {
        "desired_state_current": bool(verdict),
        "desired_state_published": (config or {}).get("desired_state_published") or "",
        "desired_state_live": (config or {}).get("desired_state_live") or "",
    }


def build_events(summaries, config, playbook, now):
    """Build the HEC event list for a finished run.

    :param summaries: mapping of inventory hostname -> Ansible stats summary
    :param config: the ``converge_telemetry`` dict published via ``set_stats``
    :param playbook: basename of the playbook that ran
    :param now: event timestamp (epoch seconds)
    """
    fqdns = config.get("fqdns") or {}
    index = config.get("index") or "ansible"
    git_sha = config.get("git_sha")
    roster = config.get("roster") or []

    # Whether the inventory this run converged against was itself current with
    # desired state, as reported by the inventory_resolve role. It rides the
    # per-host event rather than a separate sourcetype because the question it
    # answers is per-converge: "what this host was converged against was already
    # out of date". Absent when the role could not check (no store credentials,
    # or an artifact older than the fingerprint contract) — and absent must stay
    # absent rather than defaulting to True, or a converge that never checked
    # would report the artifact current.
    desired_state = _desired_state_fields(config)

    def envelope(host, sourcetype, event):
        return {
            "time": now,
            "host": host,
            "source": SOURCE,
            "sourcetype": sourcetype,
            "index": index,
            "event": event,
        }

    events = []
    for hostname in sorted(summaries):
        summary = summaries[hostname]
        fqdn = fqdns.get(hostname, hostname)
        events.append(
            envelope(
                fqdn,
                SOURCETYPE_CONVERGE,
                {
                    "status": host_status(summary),
                    "host": fqdn,
                    "inventory_hostname": hostname,
                    "playbook": playbook,
                    "repo": SOURCE,
                    "git_sha": git_sha,
                    "ok": summary.get("ok", 0),
                    "changed": summary.get("changed", 0),
                    "skipped": summary.get("skipped", 0),
                    "failures": summary.get("failures", 0),
                    "unreachable": summary.get("unreachable", 0),
                    "rescued": summary.get("rescued", 0),
                    "ignored": summary.get("ignored", 0),
                    **desired_state,
                },
            )
        )

    for hostname in sorted(roster):
        fqdn = fqdns.get(hostname, hostname)
        events.append(
            envelope(
                fqdn,
                SOURCETYPE_ROSTER,
                {
                    "host": fqdn,
                    "inventory_hostname": hostname,
                    "playbook": playbook,
                    "repo": SOURCE,
                    "git_sha": git_sha,
                },
            )
        )

    return events


def build_task_events(tasks, config, playbook, now):
    """Build one event per executed task, carrying its wall-clock duration.

    Per-task timing was previously visible ONLY in `profile_tasks` stdout, which
    is written to a local run log and shipped nowhere. So "which task is slow"
    could not be answered from the observability platform at all -- it required
    reading a log file off the machine that happened to run the converge. That
    is how a single task burning 776 seconds (12.9 min of a 40-min converge)
    stayed unnoticed: nothing was measuring it where anyone would look.

    One event per task rather than per task-and-host: this mirrors what
    `profile_tasks` reports (wall clock for the whole task across every host it
    fanned out to), which is the number that actually explains converge
    duration. Per-host granularity would multiply event volume by the roster
    size to answer a question nobody was asking.
    """
    index = config.get("index") or "ansible"
    git_sha = config.get("git_sha")

    events = []
    for task in tasks:
        events.append(
            {
                "time": task["ended"],
                "host": config.get("controller") or "controller",
                "source": SOURCE,
                "sourcetype": SOURCETYPE_TASK,
                "index": index,
                "event": {
                    "playbook": playbook,
                    "repo": SOURCE,
                    "git_sha": git_sha,
                    "task": task["name"],
                    "role": task["role"],
                    "action": task["action"],
                    "duration_seconds": round(task["duration"], 3),
                    "hosts": task["hosts"],
                    "changed": task["changed"],
                    "failed": task["failed"],
                    "unreachable": task["unreachable"],
                },
            }
        )
    return events


#: An unreachable message is a connection error, not command output, but it is
#: assembled from whatever the transport said and its length is not bounded by
#: anything this repo controls. Cap it so one pathological error cannot dominate
#: a batch; the useful part -- the transport's own reason -- is at the front.
UNREACHABLE_MSG_MAX = 512


def build_unreachable_events(unreachables, config, playbook, now):
    """One event per host that could not be reached, carrying the REASON.

    The per-host summary already records that a host was unreachable. It does
    not record why, and the counter alone cannot distinguish a guest that is
    down from a connection refused mid-handshake under load from a host key
    that no longer matches -- three faults with three different fixes that look
    identical in the summary. Answering that meant leaving the log platform for
    the runner's raw job output, which is exactly the gap this closes.

    Emitted per host rather than folded into the summary: the question is
    "which hosts, and what did the transport say", and a summary field would
    hold one reason for a run where several hosts failed differently.
    """
    index = config.get("index") or "ansible"
    git_sha = config.get("git_sha")

    return [
        {
            "time": now,
            "host": entry["host"],
            "source": SOURCE,
            "sourcetype": SOURCETYPE_UNREACHABLE,
            "index": index,
            "event": {
                "playbook": playbook,
                "repo": SOURCE,
                "git_sha": git_sha,
                "task": entry["task"],
                "reason": entry["reason"][:UNREACHABLE_MSG_MAX],
            },
        }
        for entry in unreachables
    ]


def build_interrupted_event(config, playbook, now):
    """One marker saying the run never finished.

    ``v2_playbook_on_stats`` fires only on a normal end of run, so a converge
    killed by Ctrl-C or a wrapper timeout published nothing at all -- and those
    are the runs worth seeing. Deliberately NOT a per-host freshness event: the
    run converged nothing, so publishing one would refresh the >7-day staleness
    clock for hosts never touched. Where it got to is already in the task
    events published alongside this one.
    """
    return {
        "time": now,
        "host": config.get("controller") or "controller",
        "source": SOURCE,
        "sourcetype": SOURCETYPE_INTERRUPTED,
        "index": config.get("index") or "ansible",
        "event": {
            "playbook": playbook,
            "repo": SOURCE,
            "git_sha": config.get("git_sha"),
            "status": "interrupted",
        },
    }


def encode_batch(events):
    """Encode events as the concatenated-JSON body Splunk HEC expects."""
    return "".join(json.dumps(event, sort_keys=True) for event in events)
