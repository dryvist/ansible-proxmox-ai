"""Regression tests for run-ansible.sh's converge guards: stale-checkout
(commit behind origin), dirty-tree (uncommitted edits to tracked files), and
zero-host --limit (a --limit that matched nothing beyond localhost).

The real network/OpenBao path is avoided entirely by setting
PROXMOX_SSH_KEY_PATH (the static break-glass key branch), since these tests
are about the guards that run BEFORE that branch, not about cert minting
(see test_run_ansible_identity.py for that contract).
"""

import subprocess
import unittest

from _runner_stubs import RunnerSandboxBase


class RunAnsibleGuardContract(RunnerSandboxBase):
    # --- baseline -------------------------------------------------------

    def test_clean_checkout_at_remote_head_converges(self):
        self._write_recap("localhost")
        result = self._run("--limit", "localhost")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called_log.exists())

    # --- stale-checkout (behind origin) ----------------------------------

    def test_behind_remote_refuses(self):
        # Simulate a teammate's push landing on origin after this checkout.
        other = self.work.parent / "other-clone"
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(other)], check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t2@example.com"], cwd=other, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "t2"], cwd=other, check=True
        )
        (other / "README.md").write_text("newer\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=other, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "someone else's push"], cwd=other, check=True
        )
        subprocess.run(["git", "push", "-q"], cwd=other, check=True)

        result = self._run("--limit", "localhost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("behind origin/develop", result.stderr)
        self._assert_playbook_not_called()

    def test_allow_stale_checkout_bypasses_behind_remote(self):
        other = self.work.parent / "other-clone2"
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(other)], check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t3@example.com"], cwd=other, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "t3"], cwd=other, check=True
        )
        (other / "README.md").write_text("newer\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=other, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "someone else's push"], cwd=other, check=True
        )
        subprocess.run(["git", "push", "-q"], cwd=other, check=True)

        self._write_recap("localhost")
        result = self._run("--limit", "localhost", allow_stale=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called_log.exists())

    # --- dirty tree (uncommitted edits to TRACKED files) -----------------
    # The SHA check above is blind to this: local edits deploy content that
    # matches neither the remote nor a clean checkout of HEAD, and still
    # exit 0 with a green recap — the same silent-drift shape the SHA check
    # exists to catch.

    def test_dirty_tracked_file_refuses(self):
        (self.work / "README.md").write_text("locally edited\n", encoding="utf-8")
        result = self._run("--limit", "localhost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        self._assert_playbook_not_called()

    def test_allow_stale_checkout_bypasses_dirty_tree(self):
        (self.work / "README.md").write_text("locally edited\n", encoding="utf-8")
        self._write_recap("localhost")
        result = self._run("--limit", "localhost", allow_stale=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called_log.exists())

    def test_untracked_file_does_not_trigger_dirty_guard(self):
        # This repo routinely carries untracked working state (a published
        # tofu-inventory cache, a resolved shared role) that is not playbook
        # drift — --untracked-files=no exists precisely so those don't
        # refuse every routine run.
        (self.work / "scratch-cache.json").write_text("{}", encoding="utf-8")
        self._write_recap("localhost")
        result = self._run("--limit", "localhost")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called_log.exists())

    def test_dirty_tree_guard_still_fires_on_detached_head(self):
        # The staleness (SHA-vs-branch) check is deliberately skipped on a
        # detached HEAD (no tracked branch to compare against — this is how
        # CI checks out a specific commit). Dirty-tree has no such exemption:
        # a pinned replay with local edits is still unreviewed drift.
        self._git("checkout", "-q", "--detach")
        (self.work / "README.md").write_text("locally edited\n", encoding="utf-8")
        result = self._run("--limit", "localhost")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        self._assert_playbook_not_called()

    def test_clean_detached_head_converges(self):
        self._git("checkout", "-q", "--detach")
        self._write_recap("localhost")
        result = self._run("--limit", "localhost")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called_log.exists())

    # --- zero-host --limit -------------------------------------------------

    def test_limit_beyond_localhost_with_localhost_only_recap_refuses(self):
        # The documented shape: --limit <group> without ,localhost, where the
        # inventory-loading play (hosts: localhost) still runs but the named
        # group never resolves to anything.
        self._write_recap("localhost")
        result = self._run("--limit", "hermes_agent_group")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("this run did nothing", result.stderr)

    def test_limit_beyond_localhost_with_empty_recap_refuses(self):
        # The other shape a --limit naming ONLY a dynamically-populated host
        # (e.g. --limit splunk, where the host itself is add_host'd by the
        # SAME localhost play --limit is about to filter out) produces: the
        # loader play is filtered out too, so NOTHING matches ANY play,
        # including localhost — an entirely empty recap, not merely a
        # localhost-only one. Confirms the same "recap must show a
        # non-localhost host" check covers this shape without a separate
        # branch: an empty recap trivially contains none either.
        self._write_recap()  # PLAY RECAP header present, zero host lines
        result = self._run("--limit", "splunk")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("this run did nothing", result.stderr)

    def test_limit_beyond_localhost_with_matching_host_converges(self):
        self._write_recap("localhost", "splunk")
        result = self._run("--limit", "localhost,splunk")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called_log.exists())

    def test_limit_localhost_only_never_triggers_recap_guard(self):
        # No recap file at all (simulating a play that produced no PLAY
        # RECAP section, e.g. an early failure) — the recap guard must not
        # fire when --limit never asked for anything beyond localhost.
        result = self._run("--limit", "localhost")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
