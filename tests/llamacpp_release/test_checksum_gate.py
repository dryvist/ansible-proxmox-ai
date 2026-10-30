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
PLUGIN_FILE = Path("roles/hermes_agent/defaults/main/155-plugins.yml")
FIXTURE = ROOT / "tests/llamacpp_release/fixtures/github-release-b11669.json"
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


def _run(tmp_path, *, stale=(), stale_plugin=False, fix=False, metadata=None, http_code="200", token=True):
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
    plugin_archive = b"fixture Hindsight plugin archive\n"
    plugin_sha = "0" * 64 if stale_plugin else hashlib.sha256(plugin_archive).hexdigest()
    plugin = tmp_path / PLUGIN_FILE
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(
        "hermes_agent_hindsight_plugin_revision: d952b6e655fccab4581ee0722b51c5f103873d12\n"
        f'hermes_agent_hindsight_plugin_archive_sha256: "{plugin_sha}"\n'
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
        "output = pathlib.Path(args[args.index('-o') + 1])\n"
        "url = args[-1]\n"
        "if url.startswith('https://api.github.com/'):\n"
        "    assert any(arg == 'Authorization: Bearer synthetic-test-value' for arg in args) == (os.environ['EXPECT_TOKEN'] == '1')\n"
        "elif url.startswith('https://codeload.github.com/'):\n"
        "    assert not any('Authorization:' in arg for arg in args)\n"
        "if '/releases/tags/' in url:\n"
        "    output.write_bytes(pathlib.Path(os.environ['RELEASE_FIXTURE']).read_bytes())\n"
        "elif url.startswith('https://codeload.github.com/'):\n"
        f"    output.write_bytes({plugin_archive!r})\n"
        "else:\n"
        f"    output.write_bytes({installer!r})\n"
        "print(os.environ['REPLAY_HTTP_CODE'], end='')\n"
    )
    curl.chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RELEASE_FIXTURE": str(replay), "EXPECT_TOKEN": "1" if token else "0",
        "REPLAY_HTTP_CODE": http_code,
    }
    if token:
        env["GITHUB_TOKEN"] = "synthetic-test-value"
    else:
        env.pop("GITHUB_TOKEN", None)
    result = subprocess.run(
        ["/bin/bash", str(ROOT / ".github/scripts/check-installer-sha.sh"), *( ["--fix"] if fix else [])],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
    )
    return result, release.read_text(), plugin.read_text()


def test_every_declared_asset_matches_the_official_release(tmp_path):
    result, _, _ = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(" sha matches") == 7  # Installer, plugin archive, and five assets.


@pytest.mark.parametrize("backend", BACKENDS)
def test_each_stale_asset_digest_fails_the_existing_gate(tmp_path, backend):
    result, _, _ = _run(tmp_path, stale=(backend,))
    assert result.returncode != 0
    assert "sha MISMATCH" in result.stderr


def test_fix_refreshes_all_five_declared_digests(tmp_path):
    result, refreshed, _ = _run(tmp_path, stale=BACKENDS, fix=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("FIXED ") == 5
    assert refreshed == _fixture_defaults()


def test_missing_official_asset_fails_closed(tmp_path):
    metadata = json.loads(FIXTURE.read_text())
    metadata["assets"].pop()
    result, _, _ = _run(tmp_path, metadata=metadata)
    assert result.returncode != 0
    assert "requires one official SHA-256 digest" in result.stderr


def test_wrong_release_tag_fails_closed(tmp_path):
    metadata = json.loads(FIXTURE.read_text())
    metadata["tag_name"] = "b1"
    result, _, _ = _run(tmp_path, metadata=metadata)
    assert result.returncode != 0
    assert "invalid release metadata" in result.stderr


def test_redirect_response_is_refused_without_following_auth_headers(tmp_path):
    result, _, _ = _run(tmp_path, http_code="302")
    assert result.returncode != 0
    assert "cannot fetch" in result.stderr


def test_anonymous_checksum_verification_handles_empty_auth_array_on_system_bash(tmp_path):
    result, _, _ = _run(tmp_path, token=False)
    assert result.returncode == 0, result.stderr


def test_stale_hindsight_plugin_archive_checksum_fails_and_fix_refreshes_it(tmp_path):
    result, _, _ = _run(tmp_path, stale_plugin=True)
    assert result.returncode != 0
    assert "hermes_agent_hindsight_plugin_revision" in result.stderr

    result, _, refreshed = _run(tmp_path / "fix", stale_plugin=True, fix=True)
    assert result.returncode == 0, result.stderr
    assert "FIXED hermes_agent_hindsight_plugin_revision" in result.stdout
    assert hashlib.sha256(b"fixture Hindsight plugin archive\n").hexdigest() in refreshed
