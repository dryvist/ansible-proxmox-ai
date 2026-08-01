from pathlib import Path

import jinja2


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = (REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text()
RSYSLOG = (REPO_ROOT / "roles/hermes_agent/templates/hermes-agent-rsyslog.conf.j2").read_text()
RSYSLOG_FILES_SRC = (
    REPO_ROOT / "roles/hermes_agent/templates/hermes-agent-files-rsyslog.conf.j2"
).read_text()
SYSLOG_ROUTE_TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/syslog_route.yml").read_text()
TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text()


def _render_files_template(imfile_owned_elsewhere: bool) -> str:
    env = jinja2.Environment()
    template = env.from_string(RSYSLOG_FILES_SRC)
    return template.render(
        ansible_managed="test",
        hermes_agent_imfile_owned_elsewhere=imfile_owned_elsewhere,
        hermes_agent_log_directory="/var/lib/hermes/.hermes/logs",
        hermes_agent_hermes_home="/var/lib/hermes/.hermes",
    )


def test_debug_logging_remains_enabled() -> None:
    assert "hermes_agent_log_level: DEBUG" in DEFAULTS


def test_rsyslog_routes_unit_children_and_file_logs() -> None:
    assert "$!_SYSTEMD_UNIT startswith 'hermes-'" in RSYSLOG
    assert 'File="{{ hermes_agent_log_directory }}/*.log"' in RSYSLOG_FILES_SRC
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/logs/*.log"' in RSYSLOG_FILES_SRC
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/boards/*/logs/*.log"' in RSYSLOG_FILES_SRC
    assert 'File="{{ hermes_agent_hermes_home }}/profiles/*/logs/*.log"' in RSYSLOG_FILES_SRC
    assert 'Ruleset="hermes_agent"' in RSYSLOG_FILES_SRC
    assert 'freshStartTail="off"' in RSYSLOG_FILES_SRC


def test_prompt_safe_context_metrics_are_enabled_without_verbose_payload_logging() -> None:
    assert "Enable prompt-safe Hermes request size metrics at DEBUG" in TASKS
    assert "Enable prompt-safe Hermes token usage metrics at DEBUG" in TASKS
    assert "verbose_logging: true" not in DEFAULTS


# --- Regression coverage: rsyslog imfile module-load conflict -----------------
# rsyslogd -N1 rejects a SECOND `module(load="imfile")` config-wide ("module
# 'imfile' already in this config, cannot be added") -- it is a load-once
# singleton, not something two independently-managed rsyslog.d drop-ins can
# each declare. The fix moves the file-tailing imfile inputs into their own
# later-numbered drop-in whose module load is conditional on nothing else on
# the guest already owning it (checked live in syslog_route.yml). These tests
# pin every part of that mechanism so it cannot silently regress back into an
# unconditional second `module(load="imfile")` call.


def test_main_ruleset_drop_in_never_loads_imfile_itself() -> None:
    # The 05-numbered ruleset/dispatch file must stay free of any imfile
    # module load directive (prose mentioning it, e.g. in a comment, is fine):
    # it is evaluated first (load-bearing for the `stop` that keeps hermes
    # lines out of the syslog_forwarder catch-all), so it cannot safely gamble
    # on being the sole imfile owner too.
    assert 'module(load="imfile"' not in RSYSLOG


def test_files_drop_in_loads_imfile_only_when_nothing_else_does() -> None:
    rendered_alone = _render_files_template(imfile_owned_elsewhere=False)
    assert 'module(load="imfile")' in rendered_alone

    rendered_shared = _render_files_template(imfile_owned_elsewhere=True)
    assert 'module(load="imfile")' not in rendered_shared
    # The inputs themselves are unconditional: they rely on whichever drop-in
    # actually owns the load, verified by the ordering assert in
    # syslog_route.yml rather than re-checked here.
    assert 'Ruleset="hermes_agent"' in rendered_shared


def test_syslog_route_computes_imfile_ownership_before_templating() -> None:
    assert "ansible.builtin.find" in SYSLOG_ROUTE_TASKS
    assert 'contains: \'module\\(load="imfile"\'' in SYSLOG_ROUTE_TASKS
    assert "hermes_agent_imfile_owned_elsewhere" in SYSLOG_ROUTE_TASKS
    # Excludes both of this role's own drop-ins, so a prior run of this same
    # role is never mistaken for "another" drop-in owning the load.
    assert "hermes_agent_syslog_rsyslog_config_path | basename" in SYSLOG_ROUTE_TASKS
    assert "hermes_agent_syslog_files_rsyslog_config_path | basename" in SYSLOG_ROUTE_TASKS


def test_syslog_route_asserts_ordering_instead_of_trusting_it() -> None:
    assert "Assert the other imfile loader sorts before" in SYSLOG_ROUTE_TASKS
    assert "select('lt'" in SYSLOG_ROUTE_TASKS


def test_full_config_validation_is_never_weakened() -> None:
    # The rsyslogd -N1 (whole-config) check is what caught the real conflict
    # this module addresses -- it must never be silenced or skipped.
    assert "ansible.builtin.command: rsyslogd -N1" in SYSLOG_ROUTE_TASKS
    assert "failed_when: false" not in SYSLOG_ROUTE_TASKS
    assert "ignore_errors" not in SYSLOG_ROUTE_TASKS


def test_defensive_install_comment_reflects_reality() -> None:
    # ansible-proxmox-apps's syslog_forwarder role DOES apply to this guest
    # (every lxc_containers member) -- the old comment claiming rsyslog "may
    # not be present... the AI stack does not apply the shared
    # syslog_forwarder role" was factually wrong.
    assert "the AI stack does not apply the shared syslog_forwarder role" not in SYSLOG_ROUTE_TASKS
