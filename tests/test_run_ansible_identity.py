"""Regression tests for run-ansible.sh's execution-plane identity selection:
preferring the semaphore AppRole pair, falling back to the shared ansible
pair when semaphore is absent or its login is refused, and making sure the
winning pair reaches the ansible-playbook child process.

These stub curl itself (rather than relying on PROXMOX_SSH_KEY_PATH
break-glass) to observe which sign URL and AppRole credentials the runner
actually sends. See test_run_ansible_runner.py for the converge guards
(stale-checkout, dirty-tree, zero-host --limit) that share this sandbox.
"""

import unittest
from pathlib import Path

from _runner_stubs import RunnerSandboxBase

GENERIC = {
    "SECRET_STORE_ADDR": "https://store.example.invalid",
    "SSH_SIGNER_ROLE_ID": "gen-role",
    "SSH_SIGNER_SECRET_ID": "gen-secret",
    "SSH_CA_MOUNT": "gen-mount",
    "SSH_SIGNER_ROLE": "gen-sign",
}


class RunAnsibleIdentityContract(RunnerSandboxBase):
    def test_semaphore_pair_preferred_when_both_present(self):
        self._write_fake_curl()
        self._write_recap("localhost")
        result = self._run_with_bao(
            {
                "OPENBAO_APPROLE_SEMAPHORE_ROLE_ID": "sem-role",
                "OPENBAO_APPROLE_SEMAPHORE_SECRET_ID": "sem-secret",
                "OPENBAO_APPROLE_ANSIBLE_ROLE_ID": "ans-role",
                "OPENBAO_APPROLE_ANSIBLE_SECRET_ID": "ans-secret",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("authenticated as: semaphore", result.stdout)
        self.assertNotIn("WARNING", result.stderr)
        log = self.curl_log.read_text(encoding="utf-8")
        self.assertIn("/sign/automation-semaphore", log)
        self.assertIn('"role_id":"sem-role"', log)
        self.assertIn('"secret_id":"sem-secret"', log)
        self.assertNotIn("ans-role", log)

    def test_ansible_pair_fallback_warns_and_signs_automation_ansible(self):
        self._write_fake_curl()
        self._write_recap("localhost")
        result = self._run_with_bao(
            {
                "OPENBAO_APPROLE_ANSIBLE_ROLE_ID": "ans-role",
                "OPENBAO_APPROLE_ANSIBLE_SECRET_ID": "ans-secret",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "OPENBAO_APPROLE_SEMAPHORE_ROLE_ID/OPENBAO_APPROLE_SEMAPHORE_SECRET_ID not set",
            result.stderr,
        )
        self.assertIn("authenticated as: ansible", result.stdout)
        log = self.curl_log.read_text(encoding="utf-8")
        self.assertIn("/sign/automation-ansible", log)

    def test_semaphore_login_failure_falls_back_to_ansible_pair(self):
        self._write_fake_curl()
        self._write_recap("localhost")
        result = self._run_with_bao(
            {
                "OPENBAO_APPROLE_SEMAPHORE_ROLE_ID": "sem-role",
                "OPENBAO_APPROLE_SEMAPHORE_SECRET_ID": "sem-secret",
                "OPENBAO_APPROLE_ANSIBLE_ROLE_ID": "ans-role",
                "OPENBAO_APPROLE_ANSIBLE_SECRET_ID": "ans-secret",
                "FAKE_LOGIN_FAIL_ROLE_ID": "sem-role",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("semaphore AppRole login failed", result.stderr)
        self.assertIn("authenticated as: ansible", result.stdout)
        log = self.curl_log.read_text(encoding="utf-8")
        self.assertIn("/sign/automation-ansible", log)
        self.assertIn('"role_id":"ans-role"', log)

    def test_ansible_playbook_inherits_the_winning_role_id(self):
        self._write_fake_curl()
        self._write_recap("localhost")
        child_role_id_file = Path(self.tmp.name) / "child-role-id"
        result = self._run_with_bao(
            {
                "OPENBAO_APPROLE_SEMAPHORE_ROLE_ID": "sem-role",
                "OPENBAO_APPROLE_SEMAPHORE_SECRET_ID": "sem-secret",
                "OPENBAO_APPROLE_ANSIBLE_ROLE_ID": "ans-role",
                "OPENBAO_APPROLE_ANSIBLE_SECRET_ID": "ans-secret",
                "FAKE_LOGIN_FAIL_ROLE_ID": "sem-role",
                "FAKE_CHILD_ROLE_ID_FILE": str(child_role_id_file),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(child_role_id_file.read_text(encoding="utf-8").strip(), "ans-role")

    # --- generic signer names (read first, legacy names are the fallback) --


    def test_generic_names_alone_sign_with_supplied_mount_and_role(self):
        self._write_fake_curl()
        self._write_recap("localhost")
        result = self._run_with_bao(dict(GENERIC), legacy_addr=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("authenticated as: signer", result.stdout)
        log = self.curl_log.read_text(encoding="utf-8")
        self.assertIn("https://store.example.invalid/v1/gen-mount/sign/gen-sign", log)
        self.assertIn('"role_id":"gen-role"', log)

    def test_generic_names_win_over_legacy_names(self):
        self._write_fake_curl()
        self._write_recap("localhost")
        result = self._run_with_bao(
            {
                **GENERIC,
                "OPENBAO_APPROLE_SEMAPHORE_ROLE_ID": "sem-role",
                "OPENBAO_APPROLE_SEMAPHORE_SECRET_ID": "sem-secret",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.curl_log.read_text(encoding="utf-8")
        self.assertIn("https://store.example.invalid/v1/gen-mount/sign/gen-sign", log)
        self.assertNotIn("sem-role", log)
        self.assertNotIn("bao.example.invalid", log)

    def test_generic_names_without_mount_refuse(self):
        self._write_fake_curl()
        env = dict(GENERIC)
        del env["SSH_CA_MOUNT"]
        result = self._run_with_bao(env, legacy_addr=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH_CA_MOUNT or SSH_SIGNER_ROLE is not", result.stderr)
        self._assert_playbook_not_called()
        self.assertFalse(self.curl_log.exists())


if __name__ == "__main__":
    unittest.main()
