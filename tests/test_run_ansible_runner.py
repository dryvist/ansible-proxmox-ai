"""Contract for scripts/run-ansible.sh: a thin shim that pins the shared runner and sets this repo's switches."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-ansible.sh"
SWITCHES = (
    "RUN_ANSIBLE_IDENTITIES",
    "RUN_ANSIBLE_FALLBACK_ON_ERROR",
    "RUN_ANSIBLE_EXPORT_CONVERGE_IDENTITY",
    "RUN_ANSIBLE_CHECKOUT_GUARD",
    "RUN_ANSIBLE_LIMIT_LOCALHOST",
    "RUN_ANSIBLE_RECAP_CHECK",
    "RUN_ANSIBLE_LOG_DIR",
    "RUN_ANSIBLE_RETIRING_IDENTITIES",
)
NIX_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "$SHIM_TEST_ARGV_LOG"
: > "$SHIM_TEST_SWITCHES_LOG"
for name in __SWITCHES__; do
  printf '%s=%s\n' "$name" "${!name-}" >> "$SHIM_TEST_SWITCHES_LOG"
done
"""


class RunAnsibleShimContract(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.bin_path = self.temp_path / "bin"
        self.bin_path.mkdir()
        self.argv_log = self.temp_path / "nix-argv.log"
        self.switches_log = self.temp_path / "switches.log"
        nix = self.bin_path / "nix"
        nix.write_text(NIX_STUB.replace("__SWITCHES__", " ".join(SWITCHES)), encoding="utf-8")
        nix.chmod(0o700)
        self.repo_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, args, overrides=None):
        env = os.environ.copy()
        for name in SWITCHES:
            env.pop(name, None)
        env.update(
            {
                "PATH": f"{self.bin_path}{os.pathsep}{env['PATH']}",
                "SHIM_TEST_ARGV_LOG": str(self.argv_log),
                "SHIM_TEST_SWITCHES_LOG": str(self.switches_log),
            }
        )
        env.update(overrides or {})
        return subprocess.run([str(RUNNER), *args], cwd=ROOT, env=env, check=False, capture_output=True, text=True)

    def test_pins_the_shared_runner_and_sets_this_repos_switches(self):
        result = self._run(["playbooks/site.yml", "--limit", "qdrant_group"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.argv_log.read_text(encoding="utf-8").splitlines(),
            [
                "run",
                "github:dryvist/homelab-contracts/v5.41.0#run-ansible",
                "--",
                "playbooks/site.yml",
                "--limit",
                "qdrant_group",
            ],
        )
        self.assertEqual(
            self.switches_log.read_text(encoding="utf-8").splitlines(),
            [
                "RUN_ANSIBLE_IDENTITIES=signer,semaphore?,ansible",
                "RUN_ANSIBLE_FALLBACK_ON_ERROR=1",
                "RUN_ANSIBLE_EXPORT_CONVERGE_IDENTITY=1",
                "RUN_ANSIBLE_CHECKOUT_GUARD=1",
                "RUN_ANSIBLE_LIMIT_LOCALHOST=1",
                "RUN_ANSIBLE_RECAP_CHECK=1",
                f"RUN_ANSIBLE_LOG_DIR={self.repo_root}/.ansible-run-logs",
                "RUN_ANSIBLE_RETIRING_IDENTITIES=ansible",
            ],
        )

    def test_caller_overrides_are_kept(self):
        result = self._run(
            ["playbooks/site.yml"],
            {
                "RUN_ANSIBLE_IDENTITIES": "signer",
                "RUN_ANSIBLE_LOG_DIR": "logs",
                "RUN_ANSIBLE_RETIRING_IDENTITIES": "legacy",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        switches = self.switches_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("RUN_ANSIBLE_IDENTITIES=signer", switches)
        self.assertIn("RUN_ANSIBLE_LOG_DIR=logs", switches)
        self.assertIn("RUN_ANSIBLE_RETIRING_IDENTITIES=legacy", switches)

    def test_shim_carries_no_runner_logic(self):
        text = RUNNER.read_text(encoding="utf-8")
        for needle in ("ansible-playbook", "curl", "ssh-keygen", "BAO_", "SSH_KNOWN_HOSTS"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
