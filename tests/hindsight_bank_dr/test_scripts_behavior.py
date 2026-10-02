"""Behavioural tests for the hindsight_bank_dr export/drill scripts: actually
RUN the rendered bash against stub `curl`/`docker`/`aws` executables placed
first on PATH, rather than only checking that they parse (`bash -n`) or render
(no unresolved Jinja). A syntax check cannot see a script that runs to
completion, prints result=pass, and exits 0 while having done nothing --
exactly the PAGE_IDS regression this file guards against.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import jinja2
import yaml

ROLE = Path(__file__).resolve().parent.parent.parent / "roles" / "hindsight_bank_dr"

# Same "drop unrendered-Jinja defaults" approach as test_role_files.py -- a
# plain jinja2.Environment has neither Ansible's lookup() nor its recursive
# default-templating pass, so cross-role/OpenBao-sourced values need a literal
# stand-in from CONTEXT below.
DEFAULTS = {
    key: value
    for key, value in yaml.safe_load((ROLE / "defaults" / "main.yml").read_text()).items()
    if not (isinstance(value, str) and "{{" in value)
}

CONTEXT = {
    "ansible_managed": "Ansible managed",
    "hindsight_bank_dr_container_name": "hindsight",
    "hindsight_bank_dr_api_port": 8888,
    "hindsight_bank_dr_s3_endpoint": "http://127.0.0.1:9000",
    "hindsight_bank_dr_s3_region": "us-east-1",
    "hindsight_bank_dr_s3_access_key_id": "AKIAEXAMPLE",
    "hindsight_bank_dr_s3_secret_access_key": "secretexample",  # noqa: S105 -- test fixture
    "hindsight_bank_dr_export_healthcheck_url": "",
    "hindsight_bank_dr_drill_healthcheck_url": "",
    "hindsight_bank_dr_drill_recall_query": "",
    "hindsight_bank_dr_drill_bank": "estate",
}


def _render_script(name: str, dest: Path, staging_dir: Path) -> Path:
    template = (ROLE / "templates" / name).read_text()
    context = {**DEFAULTS, **CONTEXT, "hindsight_bank_dr_staging_dir": str(staging_dir)}
    rendered = jinja2.Environment().from_string(template).render(**context)
    dest.write_text(rendered)
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC)
    return dest


def _write_stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# `date -u -d "-N days" +%Y-%m-%d` is a GNU-ism the export script's retention
# prune relies on; BSD date (macOS) has no -d. Stubbed once, shared by every
# test, so these behavioural tests run the same on a dev laptop and on CI.
_DATE_STUB = """\
for a in "$@"; do
  if [ "$a" = "-d" ]; then
    echo "1970-01-01"
    exit 0
  fi
done
exec /bin/date "$@"
"""


# In production these four arrive as real process environment via the unit's
# EnvironmentFile= (hindsight-bank-dr.env.j2) — simulate that injection here
# since these tests invoke the script directly, not through systemd.
_ENV_FILE_VARS = {
    "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "secretexample",  # noqa: S105 -- test fixture
    "AWS_DEFAULT_REGION": "us-east-1",
    "S3_ENDPOINT": "http://127.0.0.1:9000",
}


def _run(script: Path, stubdir: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{stubdir}:{os.environ['PATH']}",
        "STUBDIR": str(stubdir),
        **_ENV_FILE_VARS,
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_export_two_pages_uploads_every_bank_exactly_once(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "page0.json").write_text(
        '{"banks":[{"bank_id":"bank-a"},{"bank_id":"bank-b"}],"total":4,"limit":100,"offset":0}'
    )
    (stub / "page1.json").write_text(
        '{"banks":[{"bank_id":"bank-c"},{"bank_id":"bank-d"}],"total":4,"limit":100,"offset":2}'
    )
    _write_stub(
        stub,
        "date",
        _DATE_STUB,
    )
    _write_stub(
        stub,
        "curl",
        """\
N_FILE="$STUBDIR/curl_calls"
N=$(cat "$N_FILE" 2>/dev/null || echo 0)
echo $((N + 1)) > "$N_FILE"
case "$N" in
  0) cat "$STUBDIR/page0.json" ;;
  1) cat "$STUBDIR/page1.json" ;;
  *) echo 'unexpected extra curl call' >&2; exit 1 ;;
esac
""",
    )
    _write_stub(
        stub,
        "docker",
        """\
echo "docker $*" >> "$STUBDIR/docker_calls.log"
exit 0
""",
    )
    _write_stub(
        stub,
        "aws",
        """\
echo "aws $*" >> "$STUBDIR/aws_calls.log"
exit 0
""",
    )

    script = _render_script("hindsight-bank-export.sh.j2", tmp_path / "export.sh", tmp_path / "staging")
    result = _run(script, stub, {})

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    docker_log = (stub / "docker_calls.log").read_text()
    aws_log = (stub / "aws_calls.log").read_text()
    for bank in ("bank-a", "bank-b", "bank-c", "bank-d"):
        assert docker_log.count(f"--bank {bank} ") == 1, f"{bank} not exported exactly once: {docker_log}"
        assert aws_log.count(f"{bank}.zip") >= 1, f"{bank}.zip never uploaded: {aws_log}"
    assert '"result":"pass"' in result.stdout
    assert '"exported":4' in result.stdout


def test_export_one_bank_failure_exits_nonzero(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "page0.json").write_text(
        '{"banks":[{"bank_id":"bank-a"},{"bank_id":"bank-b"}],"total":2,"limit":100,"offset":0}'
    )
    _write_stub(stub, "date", _DATE_STUB)
    _write_stub(
        stub,
        "curl",
        """\
N_FILE="$STUBDIR/curl_calls"
N=$(cat "$N_FILE" 2>/dev/null || echo 0)
echo $((N + 1)) > "$N_FILE"
case "$N" in
  0) cat "$STUBDIR/page0.json" ;;
  *) echo 'unexpected extra curl call' >&2; exit 1 ;;
esac
""",
    )
    _write_stub(
        stub,
        "docker",
        """\
echo "docker $*" >> "$STUBDIR/docker_calls.log"
if [[ "$1" == "exec" && "$*" == *"export-bank"* && "$*" == *"--bank bank-b "* ]]; then
  exit 1
fi
exit 0
""",
    )
    _write_stub(
        stub,
        "aws",
        """\
echo "aws $*" >> "$STUBDIR/aws_calls.log"
exit 0
""",
    )

    script = _render_script("hindsight-bank-export.sh.j2", tmp_path / "export.sh", tmp_path / "staging")
    result = _run(script, stub, {})

    assert result.returncode != 0, "one bank's export failing must not exit 0"
    assert '"result":"fail"' in result.stdout
    assert '"failed":1' in result.stdout


def test_export_enumeration_finds_banks_but_uploads_none_exits_nonzero(tmp_path):
    """The exact false-green shape: enumeration returns real banks (BANK_TOTAL
    > 0) but every upload fails, so EXPORTED stays 0. Before the PAGE_IDS fix
    this was true of EVERY run (the loop body never executed), which is
    exactly why a passing `bash -n`/render test never caught it.
    """
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "page0.json").write_text(
        '{"banks":[{"bank_id":"bank-a"},{"bank_id":"bank-b"}],"total":2,"limit":100,"offset":0}'
    )
    _write_stub(stub, "date", _DATE_STUB)
    _write_stub(
        stub,
        "curl",
        """\
N_FILE="$STUBDIR/curl_calls"
N=$(cat "$N_FILE" 2>/dev/null || echo 0)
echo $((N + 1)) > "$N_FILE"
case "$N" in
  0) cat "$STUBDIR/page0.json" ;;
  *) echo 'unexpected extra curl call' >&2; exit 1 ;;
esac
""",
    )
    _write_stub(
        stub,
        "docker",
        """\
echo "docker $*" >> "$STUBDIR/docker_calls.log"
exit 0
""",
    )
    _write_stub(
        stub,
        "aws",
        """\
echo "aws $*" >> "$STUBDIR/aws_calls.log"
case " $* " in
  *" s3 cp "*) exit 1 ;;
esac
exit 0
""",
    )

    script = _render_script("hindsight-bank-export.sh.j2", tmp_path / "export.sh", tmp_path / "staging")
    result = _run(script, stub, {})

    assert result.returncode != 0, "enumeration found banks but nothing uploaded must not exit 0"
    assert '"banks_total":2' in result.stdout
    assert '"exported":0' in result.stdout
    assert '"result":"fail"' in result.stdout


def test_drill_zero_nodes_restored_exits_nonzero(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "stats.json").write_text('{"total_nodes":0,"total_documents":0}')
    _write_stub(stub, "date", _DATE_STUB)
    _write_stub(
        stub,
        "curl",
        """\
for a in "$@"; do
  case "$a" in
    DELETE) exit 0 ;;
  esac
done
for a in "$@"; do
  case "$a" in
    */stats) cat "$STUBDIR/stats.json"; exit 0 ;;
  esac
done
echo "unexpected curl call: $*" >&2
exit 1
""",
    )
    _write_stub(
        stub,
        "docker",
        """\
echo "docker $*" >> "$STUBDIR/docker_calls.log"
exit 0
""",
    )
    _write_stub(
        stub,
        "aws",
        """\
echo "aws $*" >> "$STUBDIR/aws_calls.log"
case " $* " in
  *" s3 ls "*) echo "                           PRE 2026-09-25/" ;;
esac
exit 0
""",
    )

    script = _render_script("hindsight-bank-dr-drill.sh.j2", tmp_path / "drill.sh", tmp_path / "staging")
    result = _run(script, stub, {})

    assert result.returncode != 0, "a restored bank with 0 memory units must not exit 0"
    assert '"result":"fail"' in result.stdout
    assert "0 memory units" in result.stdout
