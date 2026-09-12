"""Shared sandbox + stub helpers for run-ansible.sh tests.

Builds an ISOLATED git sandbox — a throwaway repo with its own bare
'origin' — never the real developer checkout, so tests can rewind history
and dirty files without touching real repo state. ansible-playbook and
(where needed) git itself are stubbed on PATH.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REAL_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SRC = (REAL_ROOT / "scripts" / "run-ansible.sh").read_text(encoding="utf-8")


class RunnerSandboxBase(unittest.TestCase):
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
            if [[ -n "${FAKE_CHILD_ROLE_ID_FILE:-}" ]]; then
              printf '%s\\n' "${CONVERGE_ROLE_ID:-}" > "$FAKE_CHILD_ROLE_ID_FILE"
            fi
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

    # --- execution-plane identity selection -------------------------------
    # These stub curl itself (rather than relying on PROXMOX_SSH_KEY_PATH
    # break-glass) to observe which sign URL and AppRole credentials the
    # runner actually sends.

    def _write_fake_curl(self):
        # Records every invocation's URL + request body, then answers the
        # two calls mint_ssh_cert() makes: approle login, then the CA sign.
        self._write_executable(
            "curl",
            """
            #!/usr/bin/env bash
            url="${@: -1}"
            body=""
            prev=""
            for a in "$@"; do
              if [[ "$prev" == "--data" || "$prev" == "-d" ]]; then
                if [[ "$a" == "@-" ]]; then
                  body=$(cat)
                else
                  body=$(cat "$a" 2>/dev/null || echo "$a")
                fi
              fi
              prev="$a"
            done
            printf 'URL=%s BODY=%s\\n' "$url" "$body" >> "$FAKE_CURL_LOG"
            if [[ "$url" == *"/auth/approle/login" ]]; then
              if [[ -n "${FAKE_LOGIN_FAIL_ROLE_ID:-}" && "$body" == *"\\"role_id\\":\\"$FAKE_LOGIN_FAIL_ROLE_ID\\""* ]]; then
                exit 22
              fi
              printf '{"auth":{"client_token":"fake-token"}}\\n'
            elif [[ "$url" == *"/sign/"* ]]; then
              printf '{"data":{"signed_key":"fake-cert-body"}}\\n'
            elif [[ "$url" == *"/token/revoke-self" ]]; then
              :
            fi
            exit 0
            """,
        )

    def _run_with_bao(self, env_extra, allow_stale=False):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.pop("PROXMOX_SSH_KEY_PATH", None)
        env.pop("SSH_KNOWN_HOSTS", None)
        env["FAKE_CALLED_LOG"] = str(self.called_log)
        env["FAKE_RECAP_FILE"] = str(self.recap_file)
        env["FAKE_CURL_LOG"] = str(self.curl_log)
        env["BAO_ADDR"] = "https://bao.example.invalid"
        env.update(env_extra)
        if allow_stale:
            env["ALLOW_STALE_CHECKOUT"] = "1"
        return subprocess.run(
            [str(self.runner), "playbooks/site.yml", "--limit", "localhost"],
            cwd=self.work,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
