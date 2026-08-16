"""Regression tests for run-ansible.sh's converge guards: stale-checkout
(commit behind origin), dirty-tree (uncommitted edits to tracked files), and
zero-host --limit (a --limit that matched nothing beyond localhost).

Runs the runner inside an ISOLATED git sandbox — a throwaway repo with its
own bare 'origin' — never the real developer checkout, so these tests can
rewind history and dirty files without touching real repo state. ansible-
playbook and (where needed) git itself are stubbed on PATH; the real
network/OpenBao path is avoided entirely by setting PROXMOX_SSH_KEY_PATH
(the static break-glass key branch), since these tests are about the guards
that run BEFORE that branch, not about cert minting (see
test_run_ansible_runner.py in ansible-proxmox-apps for that contract).
"""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REAL_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SRC = (REAL_ROOT / "scripts" / "run-ansible.sh").read_text(encoding="utf-8")


class RunAnsibleGuardContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        self.work = root / "work"
        self.bin = root / "bin"
        self.bin.mkdir()
        self.called_log = root / "ansible-playbook.called"
        self.recap_file = root / "recap.txt"

        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)], check=True)
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.work)], check=True
        )
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "test")
        self._git("checkout", "-q", "-b", "develop")

        scripts_dir = self.work / "scripts"
        scripts_dir.mkdir()
        self.runner = scripts_dir / "run-ansible.sh"
        self.runner.write_text(RUNNER_SRC, encoding="utf-8")
        self.runner.chmod(0o700)
        self._commit("init")
        self._git("push", "-q", "-u", "origin", "develop")
        # A fresh bare repo's HEAD defaults to whatever init.defaultBranch
        # says (often "master"/"main"), which was never pushed here — left
        # alone, a later `git clone` of origin lands on an unborn default
        # branch instead of "develop", and a commit made there never touches
        # the branch these tests are simulating a teammate's push to.
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/develop"],
            cwd=self.origin,
            check=True,
        )

        self._write_executable(
            "ansible-playbook",
            """
            #!/usr/bin/env bash
            printf 'called: %s\\n' "$*" >> "$FAKE_CALLED_LOG"
            [[ -f "$FAKE_RECAP_FILE" ]] && cat "$FAKE_RECAP_FILE"
            exit 0
            """,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.work, check=True, capture_output=True)

    def _commit(self, message, path="README.md", content="hello\n"):
        (self.work / path).write_text(content, encoding="utf-8")
        self._git("add", path)
        self._git("commit", "-q", "-m", message)

    def _write_executable(self, name, body):
        path = self.bin / name
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(0o700)

    def _run(self, *args, allow_stale=False):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env["PROXMOX_SSH_KEY_PATH"] = "/nonexistent-static-key"
        for var in (
            "BAO_ADDR",
            "OPENBAO_APPROLE_ANSIBLE_ROLE_ID",
            "OPENBAO_APPROLE_ANSIBLE_SECRET_ID",
            "SSH_KNOWN_HOSTS",
        ):
            env.pop(var, None)
        env["FAKE_CALLED_LOG"] = str(self.called_log)
        env["FAKE_RECAP_FILE"] = str(self.recap_file)
        if allow_stale:
            env["ALLOW_STALE_CHECKOUT"] = "1"
        return subprocess.run(
            [str(self.runner), "playbooks/site.yml", *args],
            cwd=self.work,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def _write_recap(self, *hosts):
        lines = ["PLAY RECAP *********************************************************"]
        lines += [f"{h} : ok=1 changed=0 unreachable=0 failed=0" for h in hosts]
        self.recap_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _assert_playbook_not_called(self):
        self.assertFalse(self.called_log.exists())

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
