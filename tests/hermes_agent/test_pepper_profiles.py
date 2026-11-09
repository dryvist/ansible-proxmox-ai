"""Exercise the production fail-closed profile guard on local fixture homes."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROLE = Path(__file__).resolve().parents[2] / 'roles/hermes_agent'


@pytest.mark.parametrize('identity', ['pepper', 'hermes', 'hermes-private', 'donna'])
@pytest.mark.parametrize('state', ['missing', 'empty', 'inherited', 'hidden', 'symlink', 'root-link'])
def test_inherited_profile_homes_fail_only_for_pepper(tmp_path, identity, state):
    profiles = tmp_path / 'home/profiles'
    profiles.parent.mkdir()
    if state != 'missing':
        profiles.mkdir()
    if state in ['inherited', 'hidden']:
        (profiles / ('.restored' if state == 'hidden' else 'restored')).mkdir()
    if state == 'symlink':
        (profiles / 'restored').symlink_to(tmp_path / 'missing-profile')
    if state == 'root-link':
        profiles.rmdir()
        profiles.symlink_to(tmp_path / 'restored')
        (tmp_path / 'restored').mkdir()
    entry = next(t for t in yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
                 if t.get('ansible.builtin.import_tasks') == 'pepper_profiles.yml')
    entry['ansible.builtin.import_tasks'] = str(ROLE / 'tasks/pepper_profiles.yml')
    playbook = tmp_path / 'verify.yml'
    playbook.write_text(yaml.safe_dump([dict(
        name='Verify inherited profile isolation', hosts='localhost', gather_facts=False,
        vars=dict(hermes_agent_identity=identity, hermes_agent_hermes_home=str(profiles.parent)),
        tasks=[entry])]))
    result = subprocess.run(
        ['ansible-playbook', str(playbook), '-i', 'localhost,', '-c', 'local', '--tags', 'hermes_profiles_env'],
        env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(tmp_path / 'local'),
                 ANSIBLE_REMOTE_TEMP=str(tmp_path / 'remote'),
                 ANSIBLE_OPENTELEMETRY_ENABLED='true',
                 ANSIBLE_OPENTELEMETRY_STORE_SPANS_IN_FILE='/dev/null'),
        text=True, capture_output=True, check=False)
    rejects = identity == 'pepper' and state not in ['missing', 'empty']
    assert result.returncode == (2 if rejects else 0), result.stdout + result.stderr
    assert '[WARNING]' not in result.stdout + result.stderr
    assert profiles.exists() == (state != 'missing')
