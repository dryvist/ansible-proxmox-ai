"""Run the shipped login and KV-read tasks under Ansible with local module stubs."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
FETCH = ROOT / "roles/openbao_secrets/tasks/fetch_domain.yml"
LOGIN_NAME = "Log in with the AppRole for {{ openbao_domain.name }}"
KV_NAME = "Read each KV path for {{ openbao_domain.name }}"
ENV_PREFIX_NAME = "Resolve role_id/secret_id from env for {{ openbao_domain.name }}"
CREDENTIALS_NAME = "Resolve role_id/secret_id values, new name, then legacy, then the operator pair for {{ openbao_domain.name }}"


def _find_task(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
        for key in ("block", "tasks", "rescue", "always"):
            if key in task:
                found = _find_task(task[key], name)
                if found:
                    return found
    return None


def _module(path, args, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from ansible.module_utils.basic import AnsibleModule\n"
        f"m = AnsibleModule(argument_spec={args!r}, supports_check_mode=True)\n"
        f"m.exit_json({result})\n",
        encoding="utf-8",
    )


def test_six_logins_report_unchanged_and_feed_the_kv_reads(tmp_path):
    tasks = yaml.safe_load(FETCH.read_text(encoding="utf-8"))
    env_prefix = _find_task(tasks, ENV_PREFIX_NAME)
    credentials = _find_task(tasks, CREDENTIALS_NAME)
    login = _find_task(tasks, LOGIN_NAME)
    kv_read = _find_task(tasks, KV_NAME)
    assert env_prefix and credentials and login and kv_read, "credential, login, and downstream KV-read tasks must exist"
    login["no_log"] = True

    collection = tmp_path / "collections/ansible_collections/community/hashi_vault"
    _module(
        collection / "plugins/modules/vault_login.py",
        {"url": {"type": "str"}, "auth_method": {"type": "str"},
         "role_id": {"type": "str"}, "secret_id": {"type": "str"},
         "validate_certs": {"type": "bool"}},
        "changed=True, login={'auth': {'client_token': 'token-' + m.params['role_id']}}",
    )
    _module(
        collection / "plugins/modules/vault_kv2_get.py",
        {"url": {"type": "str"}, "auth_method": {"type": "str"},
         "token": {"type": "str"}, "engine_mount_point": {"type": "str"},
         "path": {"type": "str"}, "validate_certs": {"type": "bool"}},
        "changed=False, secret={'token_seen': m.params['token']}",
    )

    run_tasks = tmp_path / "run_tasks.yml"
    run_tasks.write_text(
        yaml.safe_dump([
            env_prefix,
            credentials,
            login,
            {"name": "Record login status", "ansible.builtin.set_fact": {
                "test_login_changes": "{{ (test_login_changes | default([])) + [openbao_secrets_domain_login.changed] }}"
            }},
            {"ansible.builtin.set_fact": {"openbao_secrets_domain_out_of_scope": False}},
            kv_read,
            {"name": "Assert the token reached its KV read", "ansible.builtin.assert": {
                "that": [
                    "openbao_secrets_domain_reads.results[0].secret.token_seen == openbao_secrets_domain_login.login.auth.client_token"
                ]
            }},
        ], sort_keys=False),
        encoding="utf-8",
    )
    domains = [
        {"name": f"login-{index}", "paths": [f"probe/{index}"], "required": True}
        for index in range(1, 7)
    ]
    playbook = tmp_path / "playbook.yml"
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump({"all": {"hosts": {"localhost": {
            "ansible_connection": "local",
            "ansible_python_interpreter": os.sys.executable,
        }}}}),
        encoding="utf-8",
    )
    ansible_config = tmp_path / "ansible.cfg"
    ansible_config.write_text("", encoding="utf-8")
    playbook.write_text(
        yaml.safe_dump([{
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {"test_domains": domains, "openbao_secrets_bao_addr": "mock",
                     "openbao_secrets_kv_mount": "kv", "openbao_secrets_validate_certs": False,
                     "openbao_secrets_operator_domains": []},
            "tasks": [
                {"ansible.builtin.include_tasks": str(run_tasks), "loop": "{{ test_domains }}",
                 "loop_control": {"loop_var": "openbao_domain"}},
                {"ansible.builtin.assert": {"that": [
                    "test_login_changes | length == 6",
                    "test_login_changes | select('equalto', true) | list | length == 0",
                ]}},
            ],
        }], sort_keys=False),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["ANSIBLE_COLLECTIONS_PATH"] = str(tmp_path / "collections")
    env["ANSIBLE_COLLECTIONS_PATHS"] = str(tmp_path / "collections")
    env["ANSIBLE_CONFIG"] = str(ansible_config)
    for index in range(1, 7):
        prefix = re.sub("[^A-Z0-9]", "_", f"login-{index}".upper())
        env[f"OPENBAO_APPROLE_{prefix}_ROLE_ID"] = f"role-{index}"
        env[f"OPENBAO_APPROLE_{prefix}_SECRET_ID"] = f"secret-{index}"
    result = subprocess.run(
        [shutil.which("ansible-playbook"), str(playbook), "-i", str(inventory)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
