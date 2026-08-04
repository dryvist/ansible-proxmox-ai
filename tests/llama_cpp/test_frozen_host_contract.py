"""Safety contract for a llama_cpp host that is set never to serve.

WHY THIS EXISTS. `llama_cpp_service_enabled: false` is what keeps a host from
serving. The role honours it in "Enable and start llama-swap" (stopped+disabled),
but that task is not sufficient on its own: notified handlers run at the END of
the play, after it, and `systemd: state: restarted` starts a disabled unit —
`disabled` suppresses autostart, not an explicit start. So an ungated restart
handler turns "the rendered config changed" into "the service is now running",
and the converge still reports green.

These assertions pin the parts that keep the freeze real, so a future edit that
removes the gate fails here instead of on the host.
"""

from pathlib import Path

import yaml


ROLE = Path(__file__).resolve().parents[2] / "roles" / "llama_cpp"
HANDLERS = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
TASKS = (ROLE / "tasks" / "main.yml").read_text()
HOST_VARS = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "inventory" / "host_vars" / "llm-fast.yml").read_text()
)

SERVE_TOGGLE = "llama_cpp_service_enabled"
MAX_PARAM_BILLIONS = 14


def test_every_restart_handler_is_gated_on_the_serve_toggle() -> None:
    """A handler that restarts the service must not fire on a frozen host."""
    restarting = [h for h in HANDLERS if h.get("ansible.builtin.systemd", {}).get("state") == "restarted"]

    assert restarting, "expected at least one restart handler; the gate below would be vacuous without it"
    for handler in restarting:
        assert handler.get("when") == SERVE_TOGGLE, (
            f"handler {handler['name']!r} restarts llama-swap without `when: {SERVE_TOGGLE}` — "
            "a config change would start the service on a host set never to serve"
        )


def test_the_service_state_task_follows_the_serve_toggle_both_ways() -> None:
    """The toggle must drive state AND enabled, not just enablement."""
    assert f"state: \"{{{{ {SERVE_TOGGLE} | ternary('started', 'stopped') }}}}\"" in TASKS
    assert f'enabled: "{{{{ {SERVE_TOGGLE} }}}}"' in TASKS


def test_the_gpu_host_is_frozen_in_host_vars() -> None:
    """The freeze is declared, so a converge re-asserts it rather than drifting."""
    assert HOST_VARS[SERVE_TOGGLE] is False


def test_no_model_reaches_the_size_that_hard_locks_the_gpu_host() -> None:
    """Every model declares its size, and none is at or over the banned threshold."""
    models = DEFAULTS["llama_cpp_models"]

    assert models, "expected models; the size check below would be vacuous without them"
    for model in models:
        assert "param_billions" in model, f"{model['name']!r} has no param_billions to check"
        assert model["param_billions"] < MAX_PARAM_BILLIONS, (
            f"{model['name']!r} is {model['param_billions']}B, at or over the {MAX_PARAM_BILLIONS}B limit"
        )


def test_the_converge_time_size_guard_is_still_wired() -> None:
    """The role asserts the size rule itself, so an override cannot smuggle a big model in."""
    assert f"| select('ge', {MAX_PARAM_BILLIONS}) | list | length) == 0" in TASKS
    assert "rejectattr('param_billions', 'defined') | list | length) == 0" in TASKS
