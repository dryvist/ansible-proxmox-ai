"""Pepper's evidence refresh runs no hooks and persists no token."""
from pathlib import Path
import json
import os
import shlex
import subprocess

from jinja2 import Environment

ROLE = Path(__file__).resolve().parents[2] / 'roles/hermes_agent'


def test_service_refresh_bootstraps_and_updates_without_running_hooks_or_persisting_tokens(tmp_path):
    upstream = tmp_path / 'upstream'
    checkout = tmp_path / 'checkout'
    upstream.mkdir()
    checkout.mkdir()
    environment = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                       GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture',
                       GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture')

    def git(directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], env=environment,
                              check=True, capture_output=True, text=True)

    git(upstream, 'init', '--initial-branch=source')
    evidence = upstream / 'evidence.md'
    evidence.write_text('first revision')
    git(upstream, 'add', '.')
    git(upstream, '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture')
    git(checkout, 'init', '--initial-branch=evidence')
    marker = tmp_path / 'hook-ran'
    hook = checkout / '.git/hooks/post-merge'
    hook.write_text('#!/bin/sh\ntouch ' + shlex.quote(str(marker)) + '\n')
    hook.chmod(0o755)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    bao_cli = bin_dir / 'bao-approle'
    bao_cli.write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '  login)\n'
        '    [ "${PEPPER_VAULT_TEST_FAIL:-}" != login ] || exit 2\n'
        '    [ "$BAO_APPROLE_ROLE_ID" = fixture-role ] && [ "$BAO_APPROLE_SECRET_ID" = fixture-secret ] || exit 5\n'
        '    printf fixture-openbao-token\n'
        '    ;;\n'
        '  gh-token)\n'
        '    [ "${PEPPER_VAULT_TEST_FAIL:-}" != mint ] || exit 2\n'
        '    [ -z "${PEPPER_VAULT_ROLE_ID:-}" ] && [ -z "${PEPPER_VAULT_SECRET_ID:-}" ] || exit 6\n'
        '    [ "$BAO_TOKEN" = fixture-openbao-token ] || exit 3\n'
        '    [ "$2" = github-pepper ] && [ "$3" = pepper-vault-read ] || exit 4\n'
        '    printf fixture-github-token\n'
        '    ;;\n'
        '  *) exit 64 ;;\n'
        'esac\n')
    bao_cli.chmod(0o755)
    logger = bin_dir / 'logger'
    logger.write_text('#!/bin/sh\nexit 0\n')
    logger.chmod(0o755)
    helper = tmp_path / 'git-credential'
    helper.write_text((ROLE / 'files/hermes-pepper-vault-git-credential').read_text())
    helper.chmod(0o755)
    script_path = tmp_path / 'refresh.sh'
    source = (ROLE / 'templates/hermes-pepper-vault-refresh.sh.j2').read_text()
    env = Environment()
    env.filters.update(to_json=json.dumps)
    rendered = env.from_string(source).render(
        ansible_managed='managed by test',
        hermes_agent_pepper_vault_bao_addr='https://bao.example.invalid',
        hermes_agent_pepper_vault_github_token_mount='github-pepper',
        hermes_agent_pepper_vault_github_token_set='pepper-vault-read',
        hermes_agent_pepper_vault_repo_url=str(upstream),
        hermes_agent_pepper_vault_git_credential_helper_path=str(helper))
    script_path.write_text(rendered)
    script_path.chmod(0o755)
    environment.update({
        'PATH': str(bin_dir) + os.pathsep + environment.get('PATH', ''),
        'PEPPER_VAULT_ROLE_ID': 'fixture-role',
        'PEPPER_VAULT_SECRET_ID': 'fixture-secret',
    })
    for revision in ['first revision', 'second revision']:
        if revision == 'second revision':
            evidence.write_text(revision)
            git(upstream, 'add', '.')
            git(upstream, '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture update')
        subprocess.run([str(script_path)], cwd=checkout, env=environment,
                       check=True, capture_output=True, text=True)
        assert (checkout / 'evidence.md').read_text() == revision
        assert not marker.exists()
    assert 'remote "origin"' not in (checkout / '.git/config').read_text()
    assert 'fixture-github-token' not in (checkout / '.git/config').read_text()
    assert not (checkout / '.git-credentials').exists()

    current_head = git(checkout, 'rev-parse', 'HEAD').stdout.strip()
    for fail_stage in ['login', 'mint']:
        failing_env = dict(environment, PEPPER_VAULT_TEST_FAIL=fail_stage)
        result = subprocess.run([str(script_path)], cwd=checkout, env=failing_env,
                                capture_output=True, text=True)
        assert result.returncode != 0
        assert git(checkout, 'rev-parse', 'HEAD').stdout.strip() == current_head

    failed_fetch = tmp_path / 'missing-upstream'
    failed_script = env.from_string(source).render(
        ansible_managed='managed by test',
        hermes_agent_pepper_vault_bao_addr='https://bao.example.invalid',
        hermes_agent_pepper_vault_github_token_mount='github-pepper',
        hermes_agent_pepper_vault_github_token_set='pepper-vault-read',
        hermes_agent_pepper_vault_repo_url=str(failed_fetch),
        hermes_agent_pepper_vault_git_credential_helper_path=str(helper))
    failed_script_path = tmp_path / 'refresh-failed-fetch.sh'
    failed_script_path.write_text(failed_script)
    failed_script_path.chmod(0o755)
    result = subprocess.run([str(failed_script_path)], cwd=checkout, env=environment,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert git(checkout, 'rev-parse', 'HEAD').stdout.strip() == current_head
