# Copyright (c) 2026 JacobPEvans
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
"""Post per-host converge-freshness telemetry to Splunk HEC at end of run.

Why a callback and not a play: a terminal play cannot distinguish a host that
genuinely converged from a host whose failure was swallowed by the site.yml
block/rescue isolation pattern, and it has no access to per-host result counts.
This callback reads Ansible's own end-of-run ``stats`` object, so the success
verdict it publishes is derived from the executor's counters rather than from
anything a play could assert about itself.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = """
    name: converge_telemetry
    type: notification
    short_description: Ship per-host converge-freshness events to Splunk HEC
    description:
      - Emits one C(ansible:converge) event per host processed by the run,
        carrying the host FQDN, playbook name, repository git SHA and the
        per-host ok/changed/failed/unreachable/rescued counters.
      - Emits one C(ansible:converge:roster) event per inventory host so a
        Splunk alert can detect hosts that exist in inventory but have never
        reported a converge (orphans).
      - Inert unless the converging playbook publishes its configuration with
        C(ansible.builtin.set_stats) under the C(converge_telemetry) key, so
        enabling the plugin globally does not make unrelated playbooks emit.
      - Inert in check mode. A dry run changes nothing on the targets, so
        publishing a C(success) event for it would refresh the freshness clock
        of a host that was never actually converged — exactly the staleness the
        downstream alert exists to detect.
    requirements:
      - Splunk HEC endpoint reachable from the control node
    options:
      hec_token:
        description: Splunk HEC token. Never read from run stats, so it cannot
          leak into callback output or a stats dump.
        env:
          - name: SPLUNK_HEC_TOKEN
        ini:
          - section: callback_converge_telemetry
            key: hec_token
        type: str
        default: ""
      enabled:
        description: Master off switch for the emitter.
        env:
          - name: ANSIBLE_CONVERGE_TELEMETRY_ENABLED
        ini:
          - section: callback_converge_telemetry
            key: enabled
        type: bool
        default: true
"""

import json
import atexit
import importlib.util
import os
import time

from ansible import context
from ansible.module_utils.common.text.converters import to_text
from ansible.module_utils.urls import open_url
from ansible.plugins.callback import CallbackBase

#: Key the converging playbook uses with ``set_stats`` to hand this plugin its
#: tofu-derived configuration (endpoint, index, host FQDN map, git SHA).
#: The event builders live in module_utils/ so each file stays inside the
#: per-file token budget. They sit outside callback_plugins/ because the
#: loader globs every *.py there and warns about each one that is not a
#: CallbackModule. Neither directory is on sys.path, so load by path.
_EVENTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "module_utils", "converge_telemetry_events.py")
_spec = importlib.util.spec_from_file_location("converge_telemetry_events", _EVENTS_PATH)
_events = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_events)

STATS_KEY = _events.STATS_KEY
CHECK_MODE_KEY = _events.CHECK_MODE_KEY
SOURCETYPE_CONVERGE = _events.SOURCETYPE_CONVERGE
SOURCETYPE_ROSTER = _events.SOURCETYPE_ROSTER
SOURCETYPE_TASK = _events.SOURCETYPE_TASK
SOURCETYPE_INTERRUPTED = _events.SOURCETYPE_INTERRUPTED
SOURCE = _events.SOURCE
host_status = _events.host_status
is_check_mode = _events.is_check_mode
build_events = _events.build_events
build_task_events = _events.build_task_events
build_interrupted_event = _events.build_interrupted_event
build_unreachable_events = _events.build_unreachable_events
encode_batch = _events.encode_batch


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "converge_telemetry"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super(CallbackModule, self).__init__(*args, **kwargs)
        self._playbook_name = "unknown"
        # Closed-out tasks, oldest first. A task is closed when the NEXT one
        # starts (or at stats), which is how profile_tasks measures too.
        self._tasks = []
        self._open = None
        # Captured from the set_stats task as it runs, NOT at stats time --
        # see _capture_config. None until the converging playbook publishes it.
        self._config = None
        # Set the moment the normal stats path is entered, so the exit hook can
        # never double-publish a run that ended cleanly.
        self._emitted = False
        # Hosts the transport could not reach, with what it said. The summary
        # counts them; only this carries the reason.
        self._unreachable = []
        atexit.register(self._on_interpreter_exit)

    def _close_open_task(self):
        if self._open is None:
            return
        self._open["ended"] = time.time()
        self._open["duration"] = self._open["ended"] - self._open["started"]
        self._tasks.append(self._open)
        self._open = None

    def v2_playbook_on_task_start(self, task, is_conditional=False):
        self._close_open_task()
        self._open = {
            "name": task.get_name(),
            "role": str(task._role) if task._role else None,
            "action": task.action,
            "started": time.time(),
            "ended": None,
            "duration": 0.0,
            "hosts": 0,
            "changed": 0,
            "failed": 0,
            # Counted apart from `failed` on purpose -- see
            # v2_runner_on_unreachable.
            "unreachable": 0,
        }

    # Ansible routes handler tasks through their own callback; without this a
    # handler's runtime is silently attributed to whatever task preceded it.
    def v2_playbook_on_handler_task_start(self, task):
        self.v2_playbook_on_task_start(task)

    def _count(self, result, key=None):
        if self._open is None:
            return
        self._open["hosts"] += 1
        if key:
            self._open[key] += 1

    def _capture_config(self, result):
        """Take the configuration off the ``set_stats`` task's own result.

        It reaches the ``stats`` object only at end of run, so reading it there
        left an interrupted run with no endpoint to publish to. The same dict
        is in the task result, available ~40 minutes earlier on a converge.
        """
        stats = (result._result or {}).get("ansible_stats") or {}
        config = (stats.get("data") or {}).get(STATS_KEY)
        if config:
            self._config = config

    def v2_runner_on_ok(self, result):
        self._capture_config(result)
        self._count(result, "changed" if result._result.get("changed") else None)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._count(result, None if ignore_errors else "failed")

    def v2_runner_on_unreachable(self, result):
        # NOT counted as `failed`. A host the transport could not reach did not
        # fail the task -- the task never ran on it. Folding the two together
        # makes an untouched, working task look broken on however many hosts
        # were unreachable that run, which sends a reader to debug the task
        # instead of the connection. Measured: a template that had not changed
        # in either direction between two runs reported 37 "failures", all of
        # them unreachable hosts.
        self._count(result, "unreachable")
        # Captured here because this is the only place the reason exists: the
        # end-of-run summary keeps the counter and discards the message.
        #
        # Everything is read defensively. This runs on the connectivity path,
        # so an attribute this callback did not expect would turn a host that
        # could not be reached into a run that died in telemetry -- trading a
        # blip for an outage, to record a diagnostic.
        try:
            host = result._host.get_name()
        except Exception:  # noqa: BLE001 - telemetry must never fail a run
            return
        try:
            reason = to_text((result._result or {}).get("msg") or "unreachable")
        except Exception:  # noqa: BLE001 - telemetry must never fail a run
            reason = "unreachable"
        self._unreachable.append(
            {
                "host": host,
                "task": self._open["name"] if self._open else None,
                "reason": reason,
            }
        )

    def v2_runner_on_skipped(self, result):
        self._count(result)

    def v2_playbook_on_start(self, playbook):
        self._playbook_name = playbook._file_name.rsplit("/", 1)[-1]

    def v2_playbook_on_stats(self, stats):
        # Claimed BEFORE emitting, not after: if _emit raises, the run still
        # ended normally and the exit hook must not publish an "interrupted"
        # marker on top of a real end-of-run.
        self._emitted = True
        try:
            self._emit(stats)
        except Exception as exc:  # noqa: BLE001 - telemetry must never fail a run
            self._display.warning(
                "converge_telemetry: failed to publish converge freshness: %s" % to_text(exc)
            )

    def _emit(self, stats):
        if not self.get_option("enabled"):
            return

        config = self._config or (
            (getattr(stats, "custom", None) or {}).get("_run", {}).get(STATS_KEY)
        )
        if not config:
            # Not a converge run (no playbook published the configuration).
            return

        if is_check_mode(config):
            self._display.vvv(
                "converge_telemetry: check mode; converge freshness not published"
            )
            return

        url = config.get("hec_url")
        if not url:
            self._display.warning("converge_telemetry: no hec_url in run stats; nothing sent")
            return

        token = self.get_option("hec_token")
        if not token:
            self._display.warning(
                "converge_telemetry: SPLUNK_HEC_TOKEN is unset; converge freshness not published"
            )
            return

        self._close_open_task()

        summaries = {host: stats.summarize(host) for host in stats.processed}
        now = time.time()
        events = build_events(summaries, config, self._playbook_name, now)
        events.extend(
            build_task_events(self._tasks, config, self._playbook_name, now)
        )
        events.extend(
            build_unreachable_events(
                self._unreachable, config, self._playbook_name, now
            )
        )
        if not events:
            return

        self._post(url, token, events, config)
        self._display.display(
            "converge_telemetry: published %d event(s) (%d task timing(s))"
            % (len(events), len(self._tasks))
        )

    def _post(self, url, token, events, config):
        self._display.vvv("converge_telemetry: posting %d event(s) to %s" % (len(events), url))
        open_url(
            url,
            data=encode_batch(events),
            method="POST",
            headers={
                "Authorization": "Splunk %s" % token,
                "Content-Type": "application/json",
            },
            validate_certs=bool(config.get("verify_tls", False)),
            timeout=int(config.get("timeout", 10)),
        )

    def _on_interpreter_exit(self):
        """Publish what we know when the run never reached ``on_stats``.

        Ctrl-C, a wrapper timeout, or any non-zero bail leaves Ansible's stats
        callback unfired. Everything here is best-effort and must never raise:
        the process is already on its way out, and a traceback from an atexit
        hook would be the last thing printed, burying the real cause.

        SIGKILL cannot be caught, so a hard kill still publishes nothing. That
        is a genuine limit, not a case this handles.
        """
        try:
            if self._emitted:
                return
            if not self.get_option("enabled"):
                return
            config = self._config
            if not config:
                # Not a converge run, or it died before the first play's
                # set_stats -- there is no endpoint to publish to.
                return
            if is_check_mode(config):
                return
            url = config.get("hec_url")
            token = self.get_option("hec_token")
            if not url or not token:
                return

            self._close_open_task()
            now = time.time()
            events = build_task_events(self._tasks, config, self._playbook_name, now)
            events.extend(
                build_unreachable_events(
                    self._unreachable, config, self._playbook_name, now
                )
            )
            events.append(
                build_interrupted_event(config, self._playbook_name, now)
            )
            self._post(url, token, events, config)
        except Exception:  # noqa: BLE001 - never raise from an exit hook
            pass
