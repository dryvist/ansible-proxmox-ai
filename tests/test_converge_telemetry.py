"""Behavioral contracts for the converge-freshness callback plugin.

The alert built on these events is only as trustworthy as the success verdict
the plugin publishes, so these tests pin the verdict rules and the event shape.
"""

import json
import unittest

from ansible import context
from ansible.module_utils.common.collections import ImmutableDict

from converge_telemetry_support import CONFIG, RecordingCallback, summary, telemetry


class HostStatusContract(unittest.TestCase):
    def test_clean_host_is_success(self):
        self.assertEqual(telemetry.host_status(summary()), "success")

    def test_failed_host_is_not_success(self):
        self.assertEqual(telemetry.host_status(summary(failures=1)), "failed")

    def test_unreachable_host_is_not_success(self):
        self.assertEqual(telemetry.host_status(summary(unreachable=1)), "failed")

    def test_rescued_host_is_not_success(self):
        # site.yml uses rescue blocks exclusively to record isolated play
        # failures, so a rescued host did NOT converge cleanly even though
        # Ansible reports failures=0 for it.
        self.assertEqual(telemetry.host_status(summary(rescued=1)), "failed")


class EventShapeContract(unittest.TestCase):
    def setUp(self):
        self.events = telemetry.build_events(
            {"alpha": summary(), "bravo": summary(rescued=1)},
            CONFIG,
            "site.yml",
            1_700_000_000.0,
        )
        self.converge = [
            e for e in self.events if e["sourcetype"] == telemetry.SOURCETYPE_CONVERGE
        ]
        self.roster = [
            e for e in self.events if e["sourcetype"] == telemetry.SOURCETYPE_ROSTER
        ]

    def test_one_converge_event_per_processed_host(self):
        self.assertEqual(
            sorted(e["host"] for e in self.converge),
            ["alpha.example.test", "bravo.example.test"],
        )

    def test_roster_covers_every_inventory_host(self):
        self.assertEqual(
            sorted(e["host"] for e in self.roster),
            ["alpha.example.test", "bravo.example.test", "charlie.example.test"],
        )

    def test_status_reflects_the_per_host_verdict(self):
        statuses = {e["event"]["inventory_hostname"]: e["event"]["status"] for e in self.converge}
        self.assertEqual(statuses, {"alpha": "success", "bravo": "failed"})

    def test_required_alert_fields_are_present(self):
        event = self.converge[0]
        self.assertEqual(event["index"], "ansible")
        self.assertEqual(event["time"], 1_700_000_000.0)
        for field in ("host", "playbook", "git_sha", "ok", "changed",
                      "failures", "unreachable", "rescued", "status"):
            self.assertIn(field, event["event"])
        self.assertEqual(event["event"]["playbook"], "site.yml")
        self.assertEqual(event["event"]["git_sha"], CONFIG["git_sha"])

    def test_no_secret_material_is_ever_emitted(self):
        body = telemetry.encode_batch(self.events)
        self.assertNotIn("token", body.lower())

    def test_batch_is_concatenated_json_objects(self):
        body = telemetry.encode_batch(self.events)
        decoder = json.JSONDecoder()
        index, decoded = 0, 0
        while index < len(body):
            _, offset = decoder.raw_decode(body, index)
            index, decoded = offset, decoded + 1
        self.assertEqual(decoded, len(self.events))

    def test_unknown_host_falls_back_to_the_inventory_name(self):
        events = telemetry.build_events({"delta": summary()}, CONFIG, "site.yml", 0.0)
        converge = [e for e in events if e["sourcetype"] == telemetry.SOURCETYPE_CONVERGE]
        self.assertEqual(converge[0]["host"], "delta")


class FakeStats(object):
    """Minimal stand-in for Ansible's end-of-run ``AggregateStats``."""

    def __init__(self, config):
        self.custom = {"_run": {telemetry.STATS_KEY: config}}
        self.processed = {"alpha": 1}

    def summarize(self, host):  # noqa: ARG002 - one fixed clean host is enough
        return summary()


def emit_and_capture(config, cliargs):
    """Run the callback's emit path; return every ``open_url`` call it made."""
    posted = []
    original_open_url = telemetry.open_url
    original_cliargs = context.CLIARGS
    telemetry.open_url = lambda url, **kwargs: posted.append((url, kwargs))
    context.CLIARGS = ImmutableDict(cliargs)
    try:
        callback = RecordingCallback()
        callback.v2_playbook_on_stats(FakeStats(config))
    finally:
        telemetry.open_url = original_open_url
        context.CLIARGS = original_cliargs
    return posted


class CheckModeContract(unittest.TestCase):
    """A dry run must never refresh a host's converge-freshness clock.

    A ``--check`` run changes nothing on the targets, but Ansible still reports
    ``ok>0`` for every host it walked, so ``host_status`` would call it a
    success. Publishing that would make a genuinely stale host look fresh and
    silently defeat the >7-day staleness alert this telemetry exists to feed.
    """

    def test_a_real_run_does_publish(self):
        # Instrument validation: this proves the harness CAN observe an emit,
        # so a "nothing was posted" assertion below is evidence, not an
        # artefact of a test that could never fail.
        posted = emit_and_capture(CONFIG, {"check": False})
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][0], CONFIG["hec_url"])

    def test_cli_check_flag_suppresses_every_event(self):
        self.assertEqual(emit_and_capture(CONFIG, {"check": True}), [])

    def test_published_check_mode_flag_suppresses_every_event(self):
        # Covers API-driven runs (ansible-runner and friends) where CLIARGS
        # carries no --check flag at all.
        config = dict(CONFIG, **{telemetry.CHECK_MODE_KEY: True})
        self.assertEqual(emit_and_capture(config, {}), [])

    def test_absent_signals_are_not_read_as_check_mode(self):
        original = context.CLIARGS
        context.CLIARGS = ImmutableDict({})
        try:
            self.assertFalse(telemetry.is_check_mode(CONFIG))
        finally:
            context.CLIARGS = original


class DesiredStateFieldsContract(unittest.TestCase):
    """The 'was this converge fed a stale inventory?' fields.

    The alert built on them treats their absence as "not checked" and their
    presence as a real verdict, so the boundary between the two is the whole
    contract: a run that could not check must publish nothing rather than a
    default.
    """

    def converge_event(self, config):
        events = telemetry.build_events({"alpha": summary()}, config, "site.yml", 0.0)
        return next(
            e for e in events if e["sourcetype"] == telemetry.SOURCETYPE_CONVERGE
        )["event"]

    def test_no_verdict_publishes_no_claim(self):
        event = self.converge_event(CONFIG)
        for field in ("desired_state_current", "desired_state_published",
                      "desired_state_live"):
            self.assertNotIn(field, event)

    def test_stale_artifact_is_published_as_false(self):
        config = dict(CONFIG, desired_state_current=False,
                      desired_state_published="aaa", desired_state_live="bbb")
        event = self.converge_event(config)
        self.assertIs(event["desired_state_current"], False)
        self.assertEqual(event["desired_state_published"], "aaa")
        self.assertEqual(event["desired_state_live"], "bbb")

    def test_current_artifact_is_published_as_true(self):
        config = dict(CONFIG, desired_state_current=True,
                      desired_state_published="aaa", desired_state_live="aaa")
        self.assertIs(self.converge_event(config)["desired_state_current"], True)

    def test_roster_events_carry_no_verdict(self):
        # The verdict is a property of a converge, not of inventory membership.
        # Emitting it on the roster too would double every alert result row.
        config = dict(CONFIG, desired_state_current=False)
        events = telemetry.build_events({"alpha": summary()}, config, "site.yml", 0.0)
        roster = [e for e in events if e["sourcetype"] == telemetry.SOURCETYPE_ROSTER]
        self.assertTrue(roster)
        for event in roster:
            self.assertNotIn("desired_state_current", event["event"])

# Must stay LAST. unittest.main() runs at import of this line, so every class
# defined below it is silently never collected -- the suite still reports OK,
# with a smaller number nobody reads.
if __name__ == "__main__":
    unittest.main(verbosity=2)
