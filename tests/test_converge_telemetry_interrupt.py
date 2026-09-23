"""Contracts for what a converge publishes when it never finishes.

Split from test_converge_telemetry.py to keep each file inside the per-file
token budget: an agent changing the interrupt path should not have to read the
event-shape and check-mode contracts to find it.
"""

import importlib.util
import json
from pathlib import Path
import unittest

from ansible import context
from ansible.module_utils.common.collections import ImmutableDict


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "callback_plugins" / "converge_telemetry.py"

_spec = importlib.util.spec_from_file_location("converge_telemetry", PLUGIN)
telemetry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(telemetry)

CONFIG = {
    "hec_url": "https://splunk.example.test:8088/services/collector/event",
    "index": "ansible",
    "git_sha": "0123456789abcdef0123456789abcdef01234567",
    "roster": [],
    "fqdns": {},
}


class InterruptedRunEvents(unittest.TestCase):
    """A converge killed mid-flight must still say where it got to.

    ``v2_playbook_on_stats`` fires only on a normal end of run, so before this
    an interrupted converge published NOTHING -- and the killed runs are the
    ones worth seeing. These pin both halves: that the interrupted path emits,
    and that it never emits the per-host freshness events, which would refresh
    the staleness clock for hosts the run never actually converged.
    """

    def setUp(self):
        # is_check_mode consults the CLI flag; pin it so these tests describe
        # the interrupt path and not whatever the last test left behind.
        self._original_cliargs = context.CLIARGS
        context.CLIARGS = ImmutableDict({})

    def tearDown(self):
        context.CLIARGS = self._original_cliargs

    def _plugin(self, enabled=True, token="tok"):
        # set_options(direct=...) does not populate _plugin_options outside the
        # plugin loader, so get_option raises KeyError. Same shape the rest of
        # this suite uses (see RecordingCallback above).
        cb = telemetry.CallbackModule()
        cb._playbook_name = "site.yml"
        cb._plugin_options = {"enabled": enabled, "hec_token": token}
        cb.get_option = lambda name: cb._plugin_options[name]
        cb._posted = []
        cb._post = lambda url, tok, events, config: cb._posted.append(events)
        return cb

    def _task(self, name):
        class _Role:
            def __str__(self):
                return "openbao"

        class _Task:
            action = "ansible.builtin.command"
            _role = _Role()

            def get_name(self):
                return name

        return _Task()

    def _set_stats_result(self, config=None):
        payload = CONFIG if config is None else config

        class _Result:
            _result = {"ansible_stats": {"data": {"converge_telemetry": payload}}}

        return _Result()

    def test_interrupted_run_publishes_task_timing_and_a_marker(self):
        cb = self._plugin()
        cb.v2_runner_on_ok(self._set_stats_result())
        cb.v2_playbook_on_task_start(self._task("render policies"))
        cb.v2_playbook_on_task_start(self._task("write policies"))

        cb._on_interpreter_exit()

        self.assertEqual(len(cb._posted), 1, "an interrupted run must publish")
        events = cb._posted[0]
        sourcetypes = [e["sourcetype"] for e in events]
        self.assertEqual(sourcetypes.count("ansible:converge:task"), 2)
        self.assertEqual(sourcetypes.count("ansible:converge:interrupted"), 1)

        marker = [e for e in events if e["sourcetype"] == "ansible:converge:interrupted"][0]
        self.assertEqual(marker["event"]["status"], "interrupted")
        json.loads(json.dumps(events))

    def test_interrupted_run_never_publishes_host_freshness(self):
        cb = self._plugin()
        cb.v2_runner_on_ok(self._set_stats_result())
        cb.v2_playbook_on_task_start(self._task("render policies"))

        cb._on_interpreter_exit()

        sourcetypes = {e["sourcetype"] for e in cb._posted[0]}
        self.assertNotIn(
            "ansible:converge",
            sourcetypes,
            "an interrupted run converged nothing; a freshness event would "
            "silently reset the staleness alert for untouched hosts",
        )
        self.assertNotIn("ansible:converge:roster", sourcetypes)

    def test_normal_end_of_run_suppresses_the_exit_hook(self):
        cb = self._plugin()
        cb.v2_runner_on_ok(self._set_stats_result())
        cb.v2_playbook_on_task_start(self._task("render policies"))
        cb._emitted = True  # what v2_playbook_on_stats sets

        cb._on_interpreter_exit()

        self.assertEqual(cb._posted, [], "a clean run must not also report interrupted")

    def test_exit_hook_is_silent_without_a_converge_config(self):
        cb = self._plugin()
        cb.v2_playbook_on_task_start(self._task("some ad-hoc play"))

        cb._on_interpreter_exit()

        self.assertEqual(
            cb._posted, [], "a non-converge playbook has no endpoint and must stay silent"
        )

    def test_exit_hook_respects_check_mode(self):
        cb = self._plugin()
        cb.v2_runner_on_ok(self._set_stats_result(dict(CONFIG, check_mode=True)))
        cb.v2_playbook_on_task_start(self._task("render policies"))

        cb._on_interpreter_exit()

        self.assertEqual(cb._posted, [], "a dry run converged nothing; publish nothing")

    def test_exit_hook_never_raises(self):
        cb = self._plugin()
        cb.v2_runner_on_ok(self._set_stats_result())
        cb.v2_playbook_on_task_start(self._task("render policies"))

        def _boom(*args, **kwargs):
            raise RuntimeError("HEC unreachable")

        cb._post = _boom
        # The process is already exiting; a traceback here would bury the real
        # cause of the interrupt.
        cb._on_interpreter_exit()
