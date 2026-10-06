"""The watchdog passes its probe bearer to curl through stdin, never argv."""

from __future__ import annotations

import os
import subprocess

from jinja2 import Environment

from _brain_watchdog_shared import ROLE


def test_rendered_probe_command_keeps_bearer_out_of_curl_argv(tmp_path) -> None:
    env = Environment(autoescape=False)
    env.filters["bool"] = bool
    rendered = env.from_string(
        (ROLE / "templates" / "hermes-brain-watchdog.sh.j2").read_text()
    ).render(
        ansible_managed="test",
        hermes_agent_bin="/usr/bin/true",
        hermes_agent_model="test-model",
        hermes_agent_model_base_url="https://router.example/v1",
        hermes_agent_brain_watchdog_probe_timeout=5,
        hermes_agent_brain_watchdog_down_after=1,
        hermes_agent_brain_watchdog_up_after=1,
        hermes_agent_brain_watchdog_busy_grace_probes=1,
        hermes_agent_brain_watchdog_reconcile_paused=False,
        hermes_agent_brain_watchdog_ntfy_url="https://notify.example/topic",
        hermes_agent_ntfy_publish_token="",
        hermes_agent_brain_watchdog_healthcheck_url="",
        hermes_agent_hermes_home="/tmp/hermes",
        hermes_agent_brain_watchdog_flap_cooldown_seconds=60,
        hermes_agent_brain_watchdog_sustained_flap_threshold=5,
        hermes_agent_brain_watchdog_sustained_flap_seconds=600,
        hermes_agent_brain_watchdog_sustained_flap_rearm_seconds=600,
        hermes_agent_brain_dependent_cron_names=[],
    )
    probe = rendered.split("# --- Cron fleet control", 1)[0]

    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\0' \"$@\" > \"$CURL_ARGS_PATH\"\n"
        "cat > \"$CURL_STDIN_PATH\"\n"
        "printf '%s\\n200' '{\"choices\":[{\"finish_reason\":\"length\"}],"
        "\"usage\":{\"completion_tokens\":1}}'\n"
    )
    fake_curl.chmod(0o755)

    args_path = tmp_path / "curl-argv"
    stdin_path = tmp_path / "curl-stdin"
    sentinel = "test-only-bearer-sentinel"
    process_env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CURL_ARGS_PATH": str(args_path),
        "CURL_STDIN_PATH": str(stdin_path),
        "HERMES_AGENT_MODEL_API_KEY": sentinel,
    }
    result = subprocess.run(
        ["bash", "-c", f"{probe}\nprobe_state\n"],
        capture_output=True,
        check=True,
        env=process_env,
        text=True,
    )

    argv = args_path.read_bytes().split(b"\0")[:-1]
    request_headers = stdin_path.read_text()
    if result.stdout.strip() != "up":
        raise AssertionError("the watchdog probe fixture did not complete successfully")
    if b"@-" not in argv:
        raise AssertionError("the rendered curl command must read headers from stdin")
    if any(sentinel.encode() in arg for arg in argv):
        raise AssertionError("a bearer value appeared in the rendered curl argv")
    if request_headers != f"Authorization: Bearer {sentinel}\n":
        raise AssertionError("the request authorization header was not passed through stdin")
