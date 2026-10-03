"""Self-checks for the pr-reconcile script (files via templates/hermes-pr-reconcile.py.j2).

No LLM in this path: the script itself decides what to replay and signs/POSTs
the webhook payload. These tests exercise that logic directly, never a real
`gh` or network call.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from _pr_reconcile_shared import load_module


def test_routes_reflect_enabled_flags():
    both_off = load_module(
        hermes_agent_github_route_public_enabled=False,
        hermes_agent_github_route_private_enabled=False,
    )
    assert both_off.ROUTES == {
        "public": {"enabled": False, "name": "github-public",
                   "url": "http://127.0.0.1:8645/webhooks/{name}"},
        "private": {"enabled": False, "name": "github-private",
                    "url": "http://127.0.0.1:8644/webhooks/{name}"},
    }
    both_on = load_module()
    assert both_on.ROUTES["public"]["enabled"] is True
    assert both_on.ROUTES["private"]["enabled"] is True


@pytest.mark.parametrize("boundary,url", [
    ("public", "http://127.0.0.1:8645/webhooks/github-public"),
    ("private", "http://127.0.0.1:8644/webhooks/github-private"),
])
def test_each_route_posts_to_the_gateway_serving_it(monkeypatch, boundary, url):
    mod = load_module()
    seen = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return b""

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout: seen.append(req.full_url) or Resp())
    payload = {"repository": {"full_name": "o/r"}, "pull_request": {"number": 1, "head": {"sha": "a" * 40}}}
    mod.post(mod.ROUTES[boundary], "s", payload)
    assert seen == [url]


def test_nothing_to_do_when_no_route_enabled(monkeypatch):
    mod = load_module(
        hermes_agent_github_route_public_enabled=False,
        hermes_agent_github_route_private_enabled=False,
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("must not call gh when no route is enabled")

    monkeypatch.setattr(mod, "open_prs", boom)
    monkeypatch.setattr(mod, "load_env", boom)
    assert mod.main() == 0


def test_missing_secret_is_fatal_when_a_route_is_enabled(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "load_env", lambda _path: {})
    with pytest.raises(mod.ReconcileError, match="WEBHOOK_SECRET"):
        mod.main()


def test_build_payload_shape():
    mod = load_module()
    pr = {
        "number": 42,
        "head": {"sha": "abc123", "repo": {"full_name": "dryvist/widget"}},
        "base": {"ref": "develop", "repo": {"full_name": "dryvist/widget", "private": True}},
        "draft": False,
        "user": {"login": "alice"},
    }
    payload = mod.build_payload("dryvist/widget", pr)
    assert payload["action"] == "synchronize"
    assert payload["repository"] == {
        "full_name": "dryvist/widget", "private": True, "owner": {"login": "dryvist"},
    }
    assert payload["pull_request"]["number"] == 42
    assert payload["pull_request"]["head"]["sha"] == "abc123"
    assert payload["pull_request"]["base"]["ref"] == "develop"
    assert payload["sender"]["login"] == "alice"


def test_same_repo_detects_forks():
    mod = load_module()
    same = {"head": {"repo": {"full_name": "dryvist/widget"}}, "base": {"repo": {"full_name": "dryvist/widget"}}}
    fork = {"head": {"repo": {"full_name": "bob/widget"}}, "base": {"repo": {"full_name": "dryvist/widget"}}}
    deleted = {"head": {"repo": None}, "base": {"repo": {"full_name": "dryvist/widget"}}}
    assert mod.same_repo(same)
    assert not mod.same_repo(fork)
    assert not mod.same_repo(deleted)


def test_sign_matches_hmac_sha256():
    mod = load_module()
    body = b'{"a":1}'
    expected = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert mod.sign("topsecret", body) == expected


def test_already_reviewed_requires_matching_bot_and_head_sha(monkeypatch):
    mod = load_module()
    reviews = [
        {"user": {"login": "other-bot[bot]"}, "body": "<!-- hermes-review sha=headsha -->ok"},
        {"user": {"login": "jacobs-hermes-agent[bot]"}, "body": "<!-- hermes-review sha=stale -->ok"},
    ]
    monkeypatch.setattr(mod, "gh_json", lambda *_args, **_kwargs: reviews)
    assert not mod.already_reviewed("dryvist/widget", 1, "headsha", "public")

    reviews.append({"user": {"login": "jacobs-hermes-agent[bot]"}, "body": "<!-- hermes-review sha=headsha -->ok"})
    assert mod.already_reviewed("dryvist/widget", 1, "headsha", "public")


def test_gh_failure_is_fatal_not_silent(monkeypatch):
    """A `gh` failure must raise, never be reported as 'nothing to replay'."""
    mod = load_module()
    monkeypatch.setattr(mod, "load_env", lambda _path: {"WEBHOOK_SECRET": "s"})

    def failing_open_prs(_boundary):
        raise mod.ReconcileError("gh api search/issues exited 1: rate limited")

    monkeypatch.setattr(mod, "open_prs", failing_open_prs)
    with pytest.raises(mod.ReconcileError, match="rate limited"):
        mod.main()


def test_cap_stops_at_configured_limit(monkeypatch):
    import re

    mod = load_module(hermes_agent_pr_reconcile_cap=2)
    monkeypatch.setattr(mod, "load_env", lambda _path: {"WEBHOOK_SECRET": "s"})
    monkeypatch.setattr(mod, "open_prs", lambda _boundary: [(f"dryvist/r{i}", i) for i in range(5)])

    def fake_pr_detail(args, _boundary):
        match = re.match(r"repos/(.+)/pulls/(\d+)", args[1])
        assert match, f"unexpected gh_json args: {args}"
        repo, number = match.groups()
        return {
            "number": int(number),
            "head": {"sha": "s", "repo": {"full_name": repo}},
            "base": {"ref": "develop", "repo": {"full_name": repo}},
            "draft": False,
            "user": {"login": "bob"},
        }

    monkeypatch.setattr(mod, "gh_json", fake_pr_detail)
    monkeypatch.setattr(mod, "already_reviewed", lambda *_args, **_kwargs: False)
    sent = []
    monkeypatch.setattr(mod, "post", lambda _route_name, _secret, payload: sent.append(payload))
    assert mod.main() == 0
    assert len(sent) == 2


def test_jsondecodeerror_from_gh_is_fatal(monkeypatch):
    mod = load_module()

    def bad_json(_args, _boundary, _token_set="review"):
        return "not json"

    monkeypatch.setattr(mod, "gh", bad_json)
    with pytest.raises(mod.ReconcileError, match="invalid JSON"):
        mod.gh_json(["api", "repos/dryvist/widget/pulls/1"], "public")


def test_load_env_parses_quoted_values(tmp_path):
    mod = load_module()
    env_file = tmp_path / ".env"
    env_file.write_text('WEBHOOK_SECRET="abc"\n# comment\nEMPTY=\nUNQUOTED=def\n')
    env = mod.load_env(str(env_file))
    assert env == {"WEBHOOK_SECRET": "abc", "EMPTY": "", "UNQUOTED": "def"}


def test_load_env_missing_file_is_fatal():
    mod = load_module()
    with pytest.raises(mod.ReconcileError):
        mod.load_env("/nonexistent/path/.env")
