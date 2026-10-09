"""Replay authoritative release metadata through the existing checksum gate."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
RELEASE_FILE = Path("roles/llamacpp_release/defaults/main/00-release.yml")
HERMES_FILE = Path("roles/hermes_agent/defaults/main/10-installer-and-bundles.yml")
FIXTURE = ROOT / "tests/llamacpp_release/fixtures/github-release-b11459.json"
BACKENDS = ("cpu", "vulkan", "rocm", "cuda", "cuda_runtime")


def _fixture_defaults():
    defaults = (ROOT / RELEASE_FILE).read_text()
    release = json.loads(FIXTURE.read_text())
    defaults = re.sub(r'^(llamacpp_release_tag:) "[^"]+"$',
                      rf'\1 "{release["tag_name"]}"', defaults, flags=re.M)
    for variable, template in re.findall(r'^(llamacpp_release_[a-z_]+_asset): "([^"]+)"$', defaults, re.M):
        name = template.replace("{{ llamacpp_release_tag }}", release["tag_name"])
        digest = next(asset["digest"] for asset in release["assets"] if asset["name"] == name)
        sha_variable = variable.removesuffix("_asset") + "_sha256"
        defaults = re.sub(rf'^({sha_variable}:) "[a-f0-9]+"$',
                          rf'\1 "{digest.removeprefix("sha256:")}"', defaults, flags=re.M)
    return defaults


def _run(tmp_path, *, stale=(), fix=False, metadata=None, http_code="200"):
    defaults = _fixture_defaults()
    for backend in stale:
        defaults = re.sub(
            rf'^(llamacpp_release_{backend}_sha256:) "[a-f0-9]+"$',
            rf'\1 "{"0" * 64}"', defaults, flags=re.M,
        )
    release = tmp_path / RELEASE_FILE
    release.parent.mkdir(parents=True)
    release.write_text(defaults)
    installer = b"fixture installer\n"
    hermes = tmp_path / HERMES_FILE
    hermes.parent.mkdir(parents=True)
    hermes.write_text(
        'hermes_agent_version: "1.0.0"\n'
        f'hermes_agent_installer_sha256: "{hashlib.sha256(installer).hexdigest()}"\n'
    )
    replay = tmp_path / "release.json"
    replay.write_text(json.dumps(metadata or json.loads(FIXTURE.read_text())))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "assert not any('L' in arg for arg in args if arg.startswith('-'))\n"
        "assert args[-1].startswith('https://api.github.com/')\n"
        "output = pathlib.Path(args[args.index('-o') + 1])\n"
        "if '/releases/tags/' in args[-1]:\n"
        "    output.write_bytes(pathlib.Path(os.environ['RELEASE_FIXTURE']).read_bytes())\n"
        "else:\n"
        f"    output.write_bytes({installer!r})\n"
        "print(os.environ['REPLAY_HTTP_CODE'], end='')\n"
    )
    curl.chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RELEASE_FIXTURE": str(replay), "GITHUB_TOKEN": "synthetic-test-value",
        "REPLAY_HTTP_CODE": http_code,
    }
    result = subprocess.run(
        ["bash", str(ROOT / ".github/scripts/check-installer-sha.sh"), *( ["--fix"] if fix else [])],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
    )
    return result, release.read_text()


def test_every_declared_asset_matches_the_official_release(tmp_path):
    result, _ = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(" sha matches") == 6  # Existing installer + five assets.


@pytest.mark.parametrize("backend", BACKENDS)
def test_each_stale_asset_digest_fails_the_existing_gate(tmp_path, backend):
    result, _ = _run(tmp_path, stale=(backend,))
    assert result.returncode != 0
    assert "sha MISMATCH" in result.stderr


def test_fix_refreshes_all_five_declared_digests(tmp_path):
    result, refreshed = _run(tmp_path, stale=BACKENDS, fix=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("FIXED ") == 5
    assert refreshed == _fixture_defaults()


def test_missing_official_asset_fails_closed(tmp_path):
    metadata = json.loads(FIXTURE.read_text())
    metadata["assets"].pop()
    result, _ = _run(tmp_path, metadata=metadata)
    assert result.returncode != 0
    assert "requires one official SHA-256 digest" in result.stderr


def test_wrong_release_tag_fails_closed(tmp_path):
    metadata = json.loads(FIXTURE.read_text())
    metadata["tag_name"] = "b1"
    result, _ = _run(tmp_path, metadata=metadata)
    assert result.returncode != 0
    assert "invalid release metadata" in result.stderr


def test_redirect_response_is_refused_without_following_auth_headers(tmp_path):
    result, _ = _run(tmp_path, http_code="302")
    assert result.returncode != 0
    assert "cannot fetch" in result.stderr
