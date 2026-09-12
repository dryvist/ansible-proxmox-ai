"""Regression tests for run-ansible.sh's execution-plane identity selection:
preferring the semaphore AppRole pair, falling back to the shared ansible
pair when semaphore is absent or its login is refused, and making sure the
winning pair reaches the ansible-playbook child process.

These stub curl itself (rather than relying on PROXMOX_SSH_KEY_PATH
break-glass) to observe which sign URL and AppRole credentials the runner
actually sends. See test_run_ansible_runner.py for the converge guards
(stale-checkout, dirty-tree, zero-host --limit) that share this sandbox.
"""

from pathlib import Path
import unittest

from _runner_stubs import RunnerSandboxBase


class RunAnsibleIdentityContract(RunnerSandboxBase):
    def test_semaphore_pair_preferred_when_both_present(self):
        self.curl_log = Path(self.tmp.name) / "curl.log"
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
        self.curl_log = Path(self.tmp.name) / "curl.log"
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
        self.curl_log = Path(self.tmp.name) / "curl.log"
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
        self.curl_log = Path(self.tmp.name) / "curl.log"
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


if __name__ == "__main__":
    unittest.main()
