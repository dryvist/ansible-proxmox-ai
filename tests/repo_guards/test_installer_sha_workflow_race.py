"""The installer SHA workflow must compare against the checked-out file."""

from __future__ import annotations

import os
import subprocess
from base64 import b64encode
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/fix-installer-sha.yml"
INSTALLER_FILE = "roles/hermes_agent/defaults/main/10-installer-and-bundles.yml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


@pytest.mark.parametrize("branch_moves", [True, False], ids=["branch-moved", "branch-unchanged"])
def test_installer_fix_uses_event_checkout_blob_after_branch_moves(tmp_path: Path, branch_moves: bool) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["fix"]["steps"]
    checkout_step = next(step for step in steps if step.get("name") == "Checkout the PR branch")
    commit_step = next(
        step for step in steps if step.get("name") == "Commit the fix onto the Renovate branch"
    )
    shell_script = commit_step["run"]
    commit_commands = [line.strip() for line in shell_script.splitlines()]

    assert checkout_step["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert 'current_sha=$(git rev-parse "HEAD:${FILE}")' in commit_commands
    assert any(line.startswith('-f sha="$current_sha"') for line in commit_commands)
    assert '-f branch="$BRANCH"' in commit_commands
    assert "contents/${FILE}?ref=${BRANCH}" not in shell_script

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=renovate/test")
    _git(repo, "config", "user.name", "Workflow contract test")
    _git(repo, "config", "user.email", "workflow-contract")

    installer = repo / INSTALLER_FILE
    installer.parent.mkdir(parents=True)
    installer.write_text('hermes_agent_version: "1.0.0"\n')
    _git(repo, "add", INSTALLER_FILE)
    _git(repo, "commit", "-m", "test: create event checkout")
    event_head = _git(repo, "rev-parse", "HEAD")
    event_blob = _git(repo, "rev-parse", f"{event_head}:{INSTALLER_FILE}")

    if branch_moves:
        installer.write_text('hermes_agent_version: "2.0.0"\n')
        _git(repo, "commit", "-am", "test: simulate concurrent branch update")
    branch_head_blob = _git(repo, "rev-parse", f"HEAD:{INSTALLER_FILE}")
    assert (branch_head_blob != event_blob) is branch_moves

    # actions/checkout pins the event SHA, then the checksum step changes the
    # worktree file before the Contents API write.
    _git(repo, "checkout", "--detach", event_head)
    installer.write_text(
        'hermes_agent_version: "1.0.0"\nhermes_agent_installer_sha256: "'
        + "b" * 64
        + '"\n'
    )
    checked_out_blob = _git(repo, "rev-parse", f"HEAD:{INSTALLER_FILE}")
    branch_blob = _git(repo, "rev-parse", f"refs/heads/renovate/test:{INSTALLER_FILE}")
    assert branch_blob == branch_head_blob
    assert checked_out_blob == event_blob

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    writes = tmp_path / "accepted-content"
    gh = bin_dir / "gh"
    gh.write_text(
        """#!/bin/sh
set -eu
route=$2
shift 2
case "$route" in
  *'?ref='*)
    git -C "$GH_STUB_REPO" rev-parse "refs/heads/$BRANCH:$FILE"
    exit 0
    ;;
esac
method=GET
expected_sha=
content=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -X) method=$2; shift 2 ;;
    -f)
      field=${2%%=*}
      value=${2#*=}
      case "$field" in
        sha) expected_sha=$value ;;
        content) content=$value ;;
      esac
      shift 2
      ;;
    *) shift ;;
  esac
done
current_sha=$(git -C "$GH_STUB_REPO" rev-parse "refs/heads/$BRANCH:$FILE")
if [ "$method" != PUT ]; then
  echo "unexpected method: $method" >&2
  exit 2
fi
if [ "$expected_sha" != "$current_sha" ]; then
  echo "409 Conflict" >&2
  exit 22
fi
printf '%s' "$content" > "$GH_STUB_WRITES"
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    base64 = bin_dir / "base64"
    base64.write_text(
        "#!/bin/sh\nset -eu\n[ \"$1\" = -w0 ]\n/usr/bin/base64 < \"$2\" | tr -d '\\n'\n",
        encoding="utf-8",
    )
    base64.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FILE": INSTALLER_FILE,
        "BRANCH": "renovate/test",
        "GITHUB_REPOSITORY": "test-owner/test-repository",
        "GH_STUB_REPO": str(repo),
        "GH_STUB_WRITES": str(writes),
    }

    result = subprocess.run(
        ["bash", "-e", "-c", shell_script],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    if branch_moves:
        assert result.returncode != 0, result.stdout
        assert "409 Conflict" in result.stderr
        assert not writes.exists()
        assert _git(repo, "rev-parse", f"refs/heads/renovate/test:{INSTALLER_FILE}") == branch_head_blob
    else:
        assert result.returncode == 0, result.stderr
        assert writes.read_text() == b64encode(installer.read_bytes()).decode()
