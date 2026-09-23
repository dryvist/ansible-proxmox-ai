"""Behavioral contracts for the event-construction half of the callback.

Split from test_converge_telemetry.py, which holds the callback lifecycle and
the verdict rules, mirroring the plugin's own split. Everything shared lives in
converge_telemetry_support.
"""

import json
import unittest

from converge_telemetry_support import CONFIG, RecordingCallback, telemetry


class TaskTimingEvents(unittest.TestCase):
    """Per-task duration is the number that explains converge wall clock.

    It previously existed only in `profile_tasks` stdout, which is written to a
    local run log and shipped nowhere — so a single task burning 776 seconds of
    a 40-minute converge was invisible to every dashboard. These tests pin the
    timing bookkeeping, because a callback that silently records nothing looks
    exactly like a converge with no slow tasks.
    """

    def _task(self, name, action="command", role="openbao"):
        class _Role:
            def __str__(self):
                return role

        class _Task:
            def __init__(self):
                self.action = action
                self._role = _Role() if role else None

            def get_name(self):
                return name

        return _Task()

    def _result(self, changed=False):
        class _Result:
            def __init__(self):
                self._result = {"changed": changed}

        return _Result()

    def _plugin(self):
        cb = telemetry.CallbackModule()
        cb._playbook_name = "site.yml"
        return cb

    def test_durations_are_recorded_per_task(self):
        cb = self._plugin()
        cb.v2_playbook_on_task_start(self._task("slow render"))
        cb.v2_runner_on_ok(self._result(changed=True))
        cb.v2_runner_on_ok(self._result())
        cb.v2_playbook_on_task_start(self._task("fast thing"))
        cb._close_open_task()

        self.assertEqual([t["name"] for t in cb._tasks], ["slow render", "fast thing"])
        first = cb._tasks[0]
        self.assertEqual(first["hosts"], 2)
        self.assertEqual(first["changed"], 1)
        self.assertEqual(first["failed"], 0)
        self.assertGreaterEqual(first["duration"], 0.0)
        self.assertIsNotNone(first["ended"])

    def test_failed_ignored_and_unreachable_are_three_different_things(self):
        """`failed` counts hosts the task ran on and failed. Nothing else.

        This previously asserted that unreachable counted as failed, and it had
        never run — it sat below the module's entry point. Two separate reasons
        to change it: Ansible's own recap keeps the two apart, and so does this
        plugin's SUMMARY event, so only the task event conflated them.

        The cost of conflating is concrete. A template unchanged between two
        runs reported 37 "failures" that were entirely unreachable hosts, which
        points a reader at the task instead of at the connection.
        """
        cb = self._plugin()
        cb.v2_playbook_on_task_start(self._task("flaky"))
        cb.v2_runner_on_failed(self._result())
        cb.v2_runner_on_unreachable(self._result())
        cb.v2_runner_on_failed(self._result(), ignore_errors=True)
        cb._close_open_task()

        task = cb._tasks[0]
        self.assertEqual(task["hosts"], 3)
        self.assertEqual(task["failed"], 1, "ignored errors and unreachable are not failures")
        self.assertEqual(task["unreachable"], 1)

    def test_handler_tasks_are_timed_separately(self):
        cb = self._plugin()
        cb.v2_playbook_on_task_start(self._task("main work"))
        cb.v2_playbook_on_handler_task_start(self._task("restart service"))
        cb._close_open_task()

        self.assertEqual(
            [t["name"] for t in cb._tasks],
            ["main work", "restart service"],
            "a handler must not have its runtime attributed to the previous task",
        )

    def test_event_shape_carries_duration_and_identity(self):
        cb = self._plugin()
        cb.v2_playbook_on_task_start(self._task("render policies"))
        cb.v2_runner_on_ok(self._result(changed=True))
        cb._close_open_task()

        events = telemetry.build_task_events(cb._tasks, CONFIG, "site.yml", 1000.0)
        self.assertEqual(len(events), 1)
        envelope = events[0]
        self.assertEqual(envelope["sourcetype"], "ansible:converge:task")
        self.assertEqual(envelope["index"], "ansible")
        event = envelope["event"]
        self.assertEqual(event["task"], "render policies")
        self.assertEqual(event["role"], "openbao")
        self.assertEqual(event["git_sha"], CONFIG["git_sha"])
        self.assertIn("duration_seconds", event)
        self.assertEqual(event["changed"], 1)
        # Must survive the HEC encoder the transport actually uses.
        json.loads(json.dumps(envelope))

    def test_no_tasks_produces_no_task_events(self):
        self.assertEqual(telemetry.build_task_events([], CONFIG, "site.yml", 1.0), [])


class UnreachableReasonEvents(unittest.TestCase):
    """An unreachable host must ship WHY, not just that it happened.

    The per-host summary records a counter. A counter cannot tell a guest that
    is down from a connection dropped mid-handshake under load from a host key
    that no longer matches — three faults with three different fixes, identical
    in the summary. Answering that meant leaving the log platform for the
    runner's raw job output.
    """

    def _result(self, host, msg):
        class _Host:
            def get_name(self):
                return host

        class _Result:
            def __init__(self):
                self._host = _Host()
                self._result = {"msg": msg, "unreachable": True}

        return _Result()

    def _task(self, name):
        class _Task:
            def __init__(self):
                self.action = "gather_facts"
                self._role = None

            def get_name(self):
                return name

        return _Task()

    def test_the_transport_reason_is_captured_and_shipped(self):
        cb = RecordingCallback()
        cb.v2_playbook_on_task_start(self._task("Gathering Facts"))
        cb.v2_runner_on_unreachable(
            self._result("openbao-01", "Failed to connect to the host via ssh: kex_exchange")
        )

        events = telemetry.build_unreachable_events(
            cb._unreachable, CONFIG, "site.yml", 1000.0
        )
        self.assertEqual(len(events), 1)
        envelope = events[0]
        self.assertEqual(envelope["sourcetype"], "ansible:converge:unreachable")
        self.assertEqual(envelope["host"], "openbao-01")
        self.assertEqual(envelope["index"], "ansible")
        event = envelope["event"]
        self.assertIn("kex_exchange", event["reason"])
        self.assertEqual(event["task"], "Gathering Facts")
        self.assertEqual(event["git_sha"], CONFIG["git_sha"])
        # Must survive the HEC encoder the transport actually uses.
        json.loads(json.dumps(envelope))

    def test_each_host_keeps_its_own_reason(self):
        """A run where hosts fail differently must not collapse to one reason."""
        cb = RecordingCallback()
        cb.v2_playbook_on_task_start(self._task("Gathering Facts"))
        cb.v2_runner_on_unreachable(self._result("openbao-01", "connection refused"))
        cb.v2_runner_on_unreachable(self._result("openbao-20", "no route to host"))

        events = telemetry.build_unreachable_events(
            cb._unreachable, CONFIG, "site.yml", 1000.0
        )
        reasons = {e["host"]: e["event"]["reason"] for e in events}
        self.assertEqual(
            reasons,
            {"openbao-01": "connection refused", "openbao-20": "no route to host"},
        )

    def test_a_missing_message_still_produces_an_event(self):
        """A reason we cannot read is still a host that could not be reached."""
        cb = RecordingCallback()
        cb.v2_playbook_on_task_start(self._task("Gathering Facts"))
        cb.v2_runner_on_unreachable(self._result("openbao-21", None))

        events = telemetry.build_unreachable_events(
            cb._unreachable, CONFIG, "site.yml", 1000.0
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"]["reason"], "unreachable")

    def test_a_pathological_reason_cannot_dominate_a_batch(self):
        cb = RecordingCallback()
        cb.v2_playbook_on_task_start(self._task("Gathering Facts"))
        cb.v2_runner_on_unreachable(self._result("openbao-01", "x" * 50000))

        events = telemetry.build_unreachable_events(
            cb._unreachable, CONFIG, "site.yml", 1000.0
        )
        self.assertEqual(
            len(events[0]["event"]["reason"]),
            telemetry._events.UNREACHABLE_MSG_MAX,
        )

    def test_an_unreachable_host_is_not_counted_as_a_task_failure(self):
        """The task did not fail on that host — it never ran on it.

        Folding the two together makes an untouched, working task look broken
        on however many hosts were unreachable that run, which sends a reader
        to debug the task instead of the connection. That is not hypothetical:
        a template unchanged between two runs reported 37 "failures", every one
        of them an unreachable host.
        """
        cb = RecordingCallback()
        cb.v2_playbook_on_task_start(self._task("Render the config"))
        cb.v2_runner_on_unreachable(self._result("openbao-01", "no route"))
        cb._close_open_task()

        events = telemetry.build_task_events(cb._tasks, CONFIG, "site.yml", 1000.0)
        event = events[0]["event"]
        self.assertEqual(event["failed"], 0, "an unreachable host is not a failure")
        self.assertEqual(event["unreachable"], 1)
        self.assertEqual(event["hosts"], 1)

    def test_a_reachable_run_ships_no_such_events(self):
        """The quiet case: no unreachable host, no events, no noise."""
        self.assertEqual(
            telemetry.build_unreachable_events([], CONFIG, "site.yml", 1.0), []
        )

    def test_capture_never_raises_into_the_run(self):
        """A diagnostic must not turn an unreachable host into a dead run.

        This is the connectivity path: whatever the transport hands back, the
        callback has to survive it. Failing here would trade a blip for an
        outage in exchange for a log line.
        """

        class _Opaque:
            pass

        cb = RecordingCallback()
        cb.v2_playbook_on_task_start(self._task("Gathering Facts"))
        cb.v2_runner_on_unreachable(_Opaque())
        self.assertEqual(cb._unreachable, [])

# Must stay LAST. unittest.main() runs at import of this line, so every class
# defined below it is silently never collected -- the suite still reports OK,
# with a smaller number nobody reads.
if __name__ == "__main__":
    unittest.main(verbosity=2)
