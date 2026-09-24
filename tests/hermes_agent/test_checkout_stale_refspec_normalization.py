"""A checkout can end up with remote.origin.fetch naming the release tag
under refs/heads/ — `+refs/heads/<tag>:refs/remotes/origin/<tag>` — which is
exactly what ansible.builtin.git's own fetch() writes if it ever concluded
the tag was a branch (its is_remote_branch() check is a substring match
against `git ls-remote -h`, so a same-named or containing branch existing
on the remote at ANY past run is enough to bake this in permanently).
Measured live on hermes-donna's real checkout: that literal value, stored
from a run before this fix. Once stored, any LATER plain
`git fetch --tags origin` (no explicit refspec — what a bare
`ansible.builtin.git` fetch falls back to) resolves that stored refspec and
fails, even though the pin task's own explicit-refspec fetch works fine:
`fatal: couldn't find remote ref refs/heads/v2026.8.27`.

Part 1 reproduces that exact stored state and the fetch failure with real
git, against a local-only bare repo (no network), then proves the fix's
normalized value resolves it. Part 2 pins the shipped fix task's shape, so
a future edit can't silently drop the `when` gate or the normalized value.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from _role_files import role_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

TAG = "v2026.8.27"
STALE_FETCH_REFSPEC = f"+refs/heads/{TAG}:refs/remotes/origin/{TAG}"
NORMAL_FETCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


class StaleSingleBranchTagConfigReproduction(unittest.TestCase):
    """No network: `origin` is a local bare repo on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        self.checkout = root / "checkout"

        source = root / "source"
        source.mkdir()
        # -c ...gpgsign=false scopes to this throwaway fixture repo only —
        # a real signing key need not be present to run this test.
        env_cfg = ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
        _git(*env_cfg, "init", "-q", cwd=source)
        _git("config", "user.email", "test@example.invalid", cwd=source)
        _git("config", "user.name", "test", cwd=source)
        (source / "f").write_text("x")
        _git("add", "f", cwd=source)
        _git(*env_cfg, "commit", "-q", "-m", "init", cwd=source)
        _git(*env_cfg, "tag", TAG, "-m", "release", cwd=source)
        _git("clone", "-q", "--bare", str(source), str(self.origin), cwd=root)
        _git("clone", "-q", str(self.origin), str(self.checkout), cwd=root)

        # Reproduce the exact stored state measured on hermes-donna directly
        # -- git's own clone/branch heuristics for a tag named `--branch`
        # vary across versions and did not reproduce it here on git 2.47.3,
        # but the STATE this fix must handle is this stored value, however
        # it got written.
        result = _git(
            "config", "remote.origin.fetch", STALE_FETCH_REFSPEC, cwd=self.checkout
        )
        assert result.returncode == 0, result.stderr

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_bare_tags_fetch_then_fails_on_that_stored_refspec(self) -> None:
        result = _git("fetch", "--tags", "origin", cwd=self.checkout)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"refs/heads/{TAG}", result.stderr)

    def test_normalizing_the_refspec_fixes_the_bare_fetch(self) -> None:
        # The exact value the shipped git_config task writes.
        fix = _git(
            "config", "remote.origin.fetch", NORMAL_FETCH_REFSPEC,
            cwd=self.checkout,
        )
        self.assertEqual(fix.returncode, 0, fix.stderr)

        result = _git("fetch", "--tags", "origin", cwd=self.checkout)
        self.assertEqual(result.returncode, 0, result.stderr)


class NormalizeTaskShape(unittest.TestCase):
    def test_the_git_config_task_normalizes_to_the_full_fetch_refspec(self) -> None:
        tasks = role_tasks(ROLE_ROOT)
        task = next(
            t for t in tasks
            if t.get("name") == "Normalize the Hermes checkout's remote fetch refspec"
        )
        args = task["community.general.git_config"]
        self.assertEqual(args["name"], "remote.origin.fetch")
        self.assertEqual(args["scope"], "local")
        self.assertEqual(args["value"], NORMAL_FETCH_REFSPEC)

    def test_it_is_skipped_when_no_checkout_exists_yet(self) -> None:
        tasks = role_tasks(ROLE_ROOT)
        task = next(
            t for t in tasks
            if t.get("name") == "Normalize the Hermes checkout's remote fetch refspec"
        )
        self.assertEqual(task["when"], "hermes_agent_existing_checkout.stat.exists")

    def test_it_runs_before_the_pin_task(self) -> None:
        tasks = role_tasks(ROLE_ROOT)
        names = [t.get("name") for t in tasks]
        normalize_idx = names.index(
            "Normalize the Hermes checkout's remote fetch refspec"
        )
        pin_idx = names.index("Pin the Hermes checkout to the release tag")
        self.assertLess(normalize_idx, pin_idx)


if __name__ == "__main__":
    unittest.main()
