#!/usr/bin/env python3
"""gh wrapper: per-call token selection, trust-boundary pin and outbound gate.

1. The caller's boundary comes from HERMES_TRUST_BOUNDARY (public|private);
   unset or unknown refuses.
2. The target repository is taken from -R/--repo, an `api repos/<o>/<r>` path,
   a GraphQL repositoryNameWithOwner, a github.com URL argument, or the cwd's
   origin remote (read with the configured git binary). Its visibility is
   looked up with the boundary's review token; a repository on the other side,
   or one whose visibility cannot be read, is refused. A write with no
   resolvable target is refused.
3. A write to a public repository has its outgoing text (arguments, body/input
   files, stdin, decoded commit file contents) scanned; any rule hit refuses.
4. Otherwise the real gh runs with GH_TOKEN from <token_dir>/<set>-<boundary>,
   set = HERMES_GH_TOKEN_SET (default review).

Refusals exit non-zero and are logged with the rule id only.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import re
import subprocess
import sys
import syslog

CONFIG_PATH = "/etc/hermes-gh.json"
BOUNDARIES = ("public", "private")
READ_SUBS = {"view", "list", "status", "diff", "checks", "search"}
WRITE_CMDS = {"pr", "issue", "repo", "release", "label", "gist", "run", "workflow",
              "secret", "variable", "ruleset", "cache", "project"}
FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field"}
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
API_REPO_RE = re.compile(r"^/?repos/([\w.-]+)/([\w.-]+)")
URL_REPO_RE = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/|$)")
NWO_RE = re.compile(r"repositoryNameWithOwner\\?\"?\s*[:=]\s*\\?\"?([\w.-]+/[\w.-]+)")
CONTENTS_RE = re.compile(r"\"?contents\"?\s*:\s*\"([A-Za-z0-9+/=]{8,})\"")
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
NETS = [ipaddress.ip_network(n) for n in
        ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16")]
SECRET_PATH_RE = re.compile(
    r"\b(?:secrets?(?:-external)?/data/[\w/-]+|[\w-]+/token/[\w-]+|auth/approle/[\w/-]+|v1/sys/[\w/-]+)")
TOKEN_RES = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bhv[sbr]\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]


class Refuse(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


def _flag_value(argv: list[str], names: tuple[str, ...]) -> str | None:
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(n + "="):
                return a.split("=", 1)[1]
    return None


def _norm(repo: str) -> str | None:
    repo = repo.strip()
    m = URL_REPO_RE.search(repo)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return repo if REPO_RE.match(repo) else None


def target_repo(argv: list[str], text: str, origin: str | None) -> str | None:
    explicit = _flag_value(argv, ("-R", "--repo"))
    if explicit:
        return _norm(explicit)
    if argv[:1] == ["api"]:
        for a in argv[1:]:
            m = API_REPO_RE.match(a)
            if m:
                return f"{m.group(1)}/{m.group(2)}"
        m = NWO_RE.search(text)
        return m.group(1) if m else None
    if argv[:2] == ["repo", "view"] or argv[:2] == ["repo", "clone"]:
        for a in argv[2:]:
            if not a.startswith("-") and _norm(a):
                return _norm(a)
    for a in argv[1:]:
        m = URL_REPO_RE.search(a)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return _norm(origin) if origin else None


def is_write(argv: list[str], text: str) -> bool:
    if not argv:
        return False
    if argv[0] == "api":
        method = (_flag_value(argv, ("-X", "--method")) or "").upper()
        if argv[1:2] == ["graphql"]:
            return "mutation" in text
        if method:
            return method != "GET"
        return any(a in FIELD_FLAGS or a == "--input" for a in argv)
    if argv[0] in WRITE_CMDS:
        return len(argv) > 1 and argv[1] not in READ_SUBS
    return False


def outgoing_text(argv: list[str], stdin: bytes) -> str:
    parts = list(argv)
    paths = []
    for flag, val in zip(argv, argv[1:]):
        if flag in ("--body-file", "--input"):
            paths.append(val)
        elif flag in ("-F", "--field") and "=@" in val:
            paths.append(val.split("=@", 1)[1])
    for p in paths:
        if p != "-":
            try:
                with open(p, "rb") as fh:
                    parts.append(fh.read().decode(errors="replace"))
            except OSError:
                pass
    parts.append(stdin.decode(errors="replace"))
    text = "\n".join(parts)
    for blob in CONTENTS_RE.findall(text):
        try:
            text += "\n" + base64.b64decode(blob, validate=True).decode(errors="replace")
        except (binascii.Error, ValueError):
            pass
    return text


def scan(text: str, deny_suffixes: list[str], private_repos: list[str]) -> str | None:
    """Return the first matching rule id, or None when the text is clean."""
    for ip in IPV4_RE.findall(text):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if any(addr in n for n in NETS):
            return "private-address"
    low = text.lower()
    for suffix in deny_suffixes:
        if suffix and suffix.lower() in low:
            return "internal-domain"
    for repo in private_repos:
        if repo and re.search(rf"(?<![\w.-]){re.escape(repo)}(?![\w.-])", text, re.IGNORECASE):
            return "private-repo"
    if SECRET_PATH_RE.search(text):
        return "secret-path"
    if any(r.search(text) for r in TOKEN_RES):
        return "token"
    return None


def decide(argv: list[str], env: dict, stdin: bytes, cfg: dict, origin: str | None, visibility) -> str:
    """Return the token file key to run with, or raise Refuse."""
    boundary = env.get("HERMES_TRUST_BOUNDARY", "")
    if boundary not in BOUNDARIES:
        raise Refuse("no-boundary", "HERMES_TRUST_BOUNDARY must be public or private")
    kind = env.get("HERMES_GH_TOKEN_SET") or "review"
    if kind not in cfg["sets"]:
        raise Refuse("unknown-set", f"unknown HERMES_GH_TOKEN_SET {kind!r}")
    text = outgoing_text(argv, stdin)
    write = is_write(argv, text)
    repo = target_repo(argv, text, origin)
    if repo is None:
        if write:
            raise Refuse("no-target", "write with no resolvable target repository")
        return f"{kind}-{boundary}"
    private = visibility(repo, f"review-{boundary}")
    if private is None:
        raise Refuse("unknown-visibility", "target repository visibility could not be read")
    if private != (boundary == "private"):
        raise Refuse("boundary", f"target repository is outside the {boundary} boundary")
    if write and not private:
        rule = scan(text, cfg.get("deny_suffixes", []), cfg.get("private_repos", []))
        if rule:
            raise Refuse(rule, f"outgoing content matched rule {rule}")
    return f"{kind}-{boundary}"


def _read_token(cfg: dict, key: str) -> str:
    path = os.path.join(cfg["token_dir"], key)
    try:
        with open(path, encoding="utf-8") as fh:
            tok = fh.read().strip()
    except OSError:
        tok = ""
    if not tok:
        raise Refuse("no-token", f"no GitHub token at {path}")
    return tok


def main() -> int:
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    argv = sys.argv[1:]
    reads_stdin = "-" in argv or any(a.endswith("=@-") for a in argv)
    stdin = sys.stdin.buffer.read() if reads_stdin else b""
    env = dict(os.environ)
    env.pop("GITHUB_TOKEN", None)

    def visibility(repo: str, key: str) -> bool | None:
        run = subprocess.run(
            [cfg["real_gh"], "api", f"repos/{repo}", "--jq", ".private"],
            env={**env, "GH_TOKEN": _read_token(cfg, key)}, capture_output=True, text=True, check=False)
        out = run.stdout.strip()
        return {"true": True, "false": False}.get(out) if run.returncode == 0 else None

    origin = subprocess.run(
        [cfg["real_git"], "config", "--get", "remote.origin.url"], capture_output=True, text=True, check=False
    ).stdout.strip() or None
    try:
        key = decide(argv, env, stdin, cfg, origin, visibility)
        env["GH_TOKEN"] = _read_token(cfg, key)
    except Refuse as exc:
        syslog.openlog("hermes-gh")
        syslog.syslog(syslog.LOG_WARNING, f"refused rule={exc.rule} cmd={' '.join(argv[:2])}")
        print(f"gh: refused ({exc.rule}): {exc}", file=sys.stderr)
        return 3
    if stdin:
        return subprocess.run([cfg["real_gh"], *argv], env=env, input=stdin, check=False).returncode
    os.execve(cfg["real_gh"], [cfg["real_gh"], *argv], env)


if __name__ == "__main__":
    sys.exit(main())
