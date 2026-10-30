"""The repository configures the published OpenBao role with its strict inputs."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "playbooks/site.yml"
LOCAL_ROLE = ROOT / "roles/openbao_secrets"


def test_prefetch_uses_shared_role_with_repo_parity_inputs():
    plays = yaml.safe_load(SITE.read_text(encoding="utf-8"))
    prefetch = next(play for play in plays if play.get("name") == "Pre-fetch resource-domain secrets from OpenBao")
    fetch = next(
        task for task in prefetch["tasks"]
        if task.get("name") == "Fetch each resource-domain's KV subtree via its own AppRole"
    )

    assert fetch["ansible.builtin.include_role"]["name"] == "dryvist.homelab.openbao_secrets"
    assert fetch["vars"] == {
        "openbao_secrets_fail_on_missing_credentials": True,
        "openbao_secrets_fail_when_unavailable": True,
        "openbao_secrets_skip_cidr_refusals": True,
        "openbao_secrets_isolate_optional_login_failures": False,
        "openbao_secrets_strict_reads": True,
        "openbao_secrets_isolate_optional_read_failures": False,
    }
    assert not LOCAL_ROLE.exists()
