"""Self-check for the gh wrapper (files/hermes-gh.py): boundary pin and outbound gate."""
import base64
import runpy
import types
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
MOD = types.SimpleNamespace(**runpy.run_path(str(ROLE / "files" / "hermes-gh.py"), run_name="wrapper"))

CFG = {
    "sets": {"review": {}, "author": {}},
    "deny_suffixes": ["corp.example.test"],
    "private_repos": ["acme/secret-infra"],
}
VIS = {"acme/open": False, "acme/closed": True}
ORIGIN_PUBLIC = "git@github.com:acme/open.git"


def visibility(repo, key):
    assert key.startswith("review-")
    return VIS.get(repo)


def decide(argv, boundary="public", kind=None, stdin=b"", origin=None):
    env = {"HERMES_TRUST_BOUNDARY": boundary}
    if kind:
        env["HERMES_GH_TOKEN_SET"] = kind
    return MOD.decide(argv, env, stdin, CFG, origin, visibility)


def refused(argv, rule, **kw):
    with pytest.raises(MOD.Refuse) as exc:
        decide(argv, **kw)
    assert exc.value.rule == rule


# --- token selection and boundary pin ---------------------------------------

def test_selects_set_and_boundary():
    assert decide(["pr", "view", "1", "-R", "acme/open"]) == "review-public"
    assert decide(["pr", "view", "1", "-R", "acme/closed"], boundary="private") == "review-private"
    assert decide(["api", "repos/acme/open/pulls"], kind="author") == "author-public"


def test_requires_boundary_and_known_set():
    refused(["pr", "list"], "no-boundary", boundary="")
    refused(["pr", "list", "-R", "acme/open"], "unknown-set", kind="admin")


def test_refuses_cross_boundary_both_ways():
    refused(["pr", "view", "1", "-R", "acme/closed"], "boundary", boundary="public")
    refused(["pr", "view", "1", "-R", "acme/open"], "boundary", boundary="private")
    refused(["api", "repos/acme/closed/issues"], "boundary")
    refused(["pr", "view", "https://github.com/acme/closed/pull/3"], "boundary")
    refused(["pr", "view", "1"], "boundary", origin="https://github.com/acme/closed.git")


def test_unknown_visibility_and_untargeted_write_refused():
    refused(["pr", "view", "1", "-R", "acme/gone"], "unknown-visibility")
    refused(["api", "-X", "POST", "user/repos"], "no-target")
    assert decide(["search", "prs", "is:open"]) == "review-public"


def test_graphql_target_from_repository_name_with_owner():
    q = 'mutation{createCommitOnBranch(input:{branch:{repositoryNameWithOwner:"acme/closed"}}){commit{oid}}}'
    refused(["api", "graphql", "-f", f"query={q}"], "boundary")


# --- outbound gate -----------------------------------------------------------

CLEAN = "Adds a retry to the upload helper."


def test_clean_write_passes():
    assert decide(["pr", "comment", "1", "--body", CLEAN], origin=ORIGIN_PUBLIC) == "review-public"
    assert decide(["pr", "comment", "1", "--body", "see 8.8.8.8 and 192.0.2.1"], origin=ORIGIN_PUBLIC)


@pytest.mark.parametrize("text,rule", [
    ("reach 10.1.2.3", "private-address"),
    ("reach 172.20.0.9", "private-address"),
    ("reach 192.168.7.7", "private-address"),
    ("reach 100.64.1.1", "private-address"),
    ("reach 169.254.1.1", "private-address"),
    ("open https://svc.corp.example.test/x", "internal-domain"),
    ("mirrors acme/secret-infra", "private-repo"),
    ("read secret/data/app/example", "secret-path"),
    ("mint example-mount/token/example-set", "secret-path"),
    ("tok ghs_" + "a" * 36, "token"),
    ("key -----BEGIN OPENSSH PRIVATE KEY-----", "token"),
])
def test_each_rule_blocks_public_write(text, rule):
    refused(["pr", "comment", "1", "-R", "acme/open", "--body", text], rule)


def test_gate_reads_body_file_and_stdin(tmp_path):
    f = tmp_path / "body.md"
    f.write_text("see 10.0.0.1")
    refused(["pr", "create", "-R", "acme/open", "--body-file", str(f)], "private-address")
    refused(["pr", "review", "1", "-R", "acme/open", "--body-file", "-"], "private-address",
            stdin=b"see 10.0.0.1")


def test_gate_decodes_commit_file_contents():
    blob = base64.b64encode(b"host = 10.9.9.9\n").decode()
    q = ('mutation{createCommitOnBranch(input:{branch:{repositoryNameWithOwner:"acme/open"},'
         'fileChanges:{additions:[{path:"a",contents:"' + blob + '"}]}}){commit{oid}}}')
    refused(["api", "graphql", "-f", f"query={q}"], "private-address", kind="author")


def test_gate_skips_reads_and_private_targets():
    assert decide(["pr", "view", "1", "-R", "acme/open", "--comments"]) == "review-public"
    assert decide(["pr", "comment", "1", "-R", "acme/closed", "--body", "see 10.0.0.1"],
                  boundary="private") == "review-private"


def test_write_classification():
    assert MOD.is_write(["api", "repos/a/b/issues", "-f", "title=x"], "")
    assert not MOD.is_write(["api", "repos/a/b/issues"], "")
    assert MOD.is_write(["api", "-X", "PATCH", "repos/a/b/pulls/1"], "")
    assert not MOD.is_write(["api", "graphql", "-f", "query={viewer{login}}"], "query{viewer{login}}")
    assert MOD.is_write(["pr", "merge", "1"], "")
    assert not MOD.is_write(["pr", "diff", "1"], "")
    assert not MOD.is_write(["auth", "status"], "")
