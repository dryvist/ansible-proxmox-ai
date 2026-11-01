"""Pytest lifecycle for the pinned, role-rendered gateway fixture."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from gateway_falsifier_contract import (
    COMPOSE_FILE,
    ENVOY_IMAGE,
    PROTOCOL_VERSION,
    REPO_ROOT,
    TESTBOX_IMAGE,
    TESTBOX_SOURCE_COMMIT,
    VIKUNJA_CALLER_MARKERS,
)
from gateway_observer_helpers import (
    assert_backend_calls,
    gateway_config,
    image_identity,
    load_traces,
    observer_positive_control,
    wait_for_admin,
    wait_for_capture,
    wait_for_gateway,
)
from test_authz_render import ROLE_DEFAULTS
from vikunja_observer_helpers import (
    gateway_sidecar_credentials_stripped,
    sidecar_api_auth_matches,
    sidecar_api_has_no_caller_material,
    sidecar_api_positive_control,
    sidecar_listener_positive_control,
    vikunja_request_matches,
    wait_for_sidecar,
)

VIKUNJA_SUPPORT_DIR = REPO_ROOT / "integration" / "vikunja-read-token"
if str(VIKUNJA_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(VIKUNJA_SUPPORT_DIR))

from vikunja_read_token_support import (
    _FIXTURE_SECRETS,
    FIXTURE_TASK_TITLE,
    VIKUNJA_IMAGE,
    _api,
    _compose_file,
    _provision_vikunja_fixture,
    _published_url,
    _render_sidecar,
    _wait_for_vikunja,
)


@pytest.fixture
def runtime(request: Any) -> Any:
    mode = getattr(request, "param", None)
    if mode not in ("testbox", "vikunja"):
        raise AssertionError("runtime mode must be testbox or vikunja")
    vikunja_port = 8002 if mode == "testbox" else 8003

    image = str(ROLE_DEFAULTS["agentgateway_docker_image"])
    assert re.fullmatch(r".+:v1\.6\.0@sha256:[0-9a-f]{64}", image), (
        "the falsifier requires the digest-pinned agentgateway v1.6.0 role image"
    )
    source_dir = Path(
        os.environ.get("AGENTGATEWAY_SOURCE_DIR", REPO_ROOT / ".agentgateway")
    )
    source_commit = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_commit == TESTBOX_SOURCE_COMMIT, (
        "testbox source is not the reviewed upstream commit"
    )

    root = Path(os.environ.get("FALSIFIER_RUNTIME_DIR", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="agentgateway-falsifier-", dir=root))
    socket_dir, trace_dir = run_dir / "envoy", run_dir / "tap"
    gateway_sidecar_trace_dir = trace_dir / "gateway-sidecar"
    sidecar_api_trace_dir = trace_dir / "sidecar-api"
    for directory in (
        socket_dir,
        trace_dir,
        gateway_sidecar_trace_dir,
        sidecar_api_trace_dir,
    ):
        directory.mkdir(mode=0o777)
        directory.chmod(0o777)

    config_path = run_dir / "config.yaml"
    rendered = gateway_config(vikunja_port=vikunja_port)
    config_path.write_text(rendered)

    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT")
        if key in os.environ
    }
    env["DOCKER_CONFIG"] = str(run_dir / "empty-docker-config")
    Path(env["DOCKER_CONFIG"]).mkdir()
    env.update(
        {
            "AGENTGATEWAY_IMAGE": image,
            "GATEWAY_CONFIG": str(config_path),
            "ENVOY_RUNTIME_DIR": str(socket_dir),
            "TAP_OUTPUT_DIR": str(trace_dir),
        }
    )

    project = f"gateway-falsifier-{os.getpid()}"
    compose_config = yaml.safe_load(COMPOSE_FILE.read_text())
    observer_volumes = compose_config["services"]["observer"]["volumes"]
    envoy_mount = "./envoy.yaml:/etc/envoy/envoy.yaml:ro"
    assert observer_volumes.count(envoy_mount) == 1, (
        "gateway fixture observer must have one Envoy configuration bind mount"
    )
    observer_volumes[observer_volumes.index(envoy_mount)] = (
        f"{COMPOSE_FILE.resolve().parent / 'envoy.yaml'}:/etc/envoy/envoy.yaml:ro"
    )
    if mode == "vikunja":
        jwt_secret = secrets.token_urlsafe(48)
        _FIXTURE_SECRETS.append(jwt_secret)
        vikunja_compose_file = _compose_file(run_dir, jwt_secret, project)
        vikunja_config = yaml.safe_load(vikunja_compose_file.read_text())
        compose_config["services"]["vikunja"] = vikunja_config["services"]["vikunja"]
        compose_config["services"]["vikunja"]["networks"] = ["backend", "fixture"]
        compose_config["networks"]["fixture"] = vikunja_config["networks"]["fixture"]
        vikunja_compose_file.unlink(missing_ok=True)
    compose_path = run_dir / "gateway-vikunja-compose.yml"
    compose_path.write_text(yaml.safe_dump(compose_config, sort_keys=False))

    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(compose_path),
    ]

    def compose_run(*args: str) -> None:
        subprocess.run(
            [*compose, *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    context: dict[str, Any] = {
        "mode": mode,
        "socket_path": socket_dir / "admin.sock",
        "trace_dir": trace_dir,
        "gateway_sidecar_trace_dir": gateway_sidecar_trace_dir,
        "sidecar_api_trace_dir": sidecar_api_trace_dir,
        "caller_markers": set(VIKUNJA_CALLER_MARKERS.values()),
    }
    stack_started = False
    test_started = False
    try:
        stack_started = True
        if mode == "vikunja":
            compose_run("up", "--detach", "--no-build", "vikunja")
            vikunja_url = _published_url(
                compose_path, project, "vikunja", 3456, env=env
            )
            _wait_for_vikunja(vikunja_url)
            info_status, info, _headers = _api(vikunja_url, "GET", "/api/v1/info")
            assert info_status == 200 and info.get("version") == "v2.7.1", (
                "the pinned Vikunja fixture did not report version v2.7.1"
            )
            fixture = _provision_vikunja_fixture(vikunja_url, include_write_token=False)

            sidecar_service = _render_sidecar(
                token=fixture["read_token"],
                port=8002,
                api_url="http://observer:3456/api/v1",
                network="front",
            )
            expected_sidecar_image = str(
                ROLE_DEFAULTS["agentgateway_docker_vikunja_mcp_image"]
            )
            assert sidecar_service["image"] == expected_sidecar_image, (
                "the fixture did not use the role-pinned supergateway image"
            )
            assert (
                ROLE_DEFAULTS["agentgateway_docker_vikunja_mcp_version"] == "0.2.0"
            ), "the role-rendered Vikunja MCP package is not pinned to 0.2.0"
            assert any(
                "vikunja-mcp@0.2.0" in str(argument)
                for argument in sidecar_service.get("command", [])
            ), "the fixture did not render the role-pinned Vikunja MCP package"
            assert (
                sidecar_service["environment"]["VIKUNJA_URL"]
                == "http://observer:3456/api/v1"
            ), "the role-rendered sidecar does not use the API tap listener"

            compose_config["services"]["vikunja-mcp-sidecar"] = sidecar_service
            compose_path.write_text(yaml.safe_dump(compose_config, sort_keys=False))
            compose_run(
                "up",
                "--detach",
                "--no-build",
                "observer",
                "vikunja-mcp-sidecar",
            )
        else:
            compose_run("up", "--detach", "--no-build", "observer", "testbox")
        wait_for_admin(context["socket_path"])
        if mode == "testbox":
            observer_positive_control(context)
        else:
            sidecar_url = _published_url(
                compose_path,
                project,
                "vikunja-mcp-sidecar",
                8002,
                env=env,
            )
            sidecar_url = f"{sidecar_url}/mcp"
            context.update(
                {
                    "sidecar_url": sidecar_url,
                    "vikunja_task_id": fixture["task_id"],
                    "vikunja_task_title": FIXTURE_TASK_TITLE,
                    "vikunja_read_token": fixture["read_token"],
                    "vikunja_version": info["version"],
                }
            )
            wait_for_sidecar(sidecar_url)
            sidecar_listener_positive_control(context)
            sidecar_api_positive_control(context)

        compose_run("up", "--detach", "--no-build", "agentgateway")
        wait_for_gateway()
        context["rendered_config_sha256"] = hashlib.sha256(
            rendered.encode()
        ).hexdigest()
        context["source_commit"] = source_commit
        context["images"] = {
            "agentgateway": image_identity(env, image),
            "envoy": image_identity(env, ENVOY_IMAGE),
        }
        if mode == "testbox":
            context["images"]["testbox"] = image_identity(env, TESTBOX_IMAGE)
        else:
            context["images"].update(
                {
                    "vikunja": image_identity(env, VIKUNJA_IMAGE),
                    "vikunja_mcp": image_identity(env, expected_sidecar_image),
                }
            )
        test_started = True
        yield context
    finally:
        try:
            if stack_started:
                try:
                    if test_started:
                        compose_run("stop", "--timeout", "3", "agentgateway")
                        if mode == "vikunja":
                            compose_run("stop", "--timeout", "3", "vikunja-mcp-sidecar")

                        listeners = {
                            "gateway_to_mcp": ("mcp_observer", trace_dir),
                            "gateway_to_sidecar": (
                                "gateway_sidecar",
                                gateway_sidecar_trace_dir,
                            ),
                            "sidecar_to_api": ("sidecar_api", sidecar_api_trace_dir),
                        }
                        active_prefixes = (
                            {"mcp_observer"}
                            if mode == "testbox"
                            else {"gateway_sidecar", "sidecar_api"}
                        )
                        counts: dict[str, int] = {}
                        traces: dict[str, list[dict[str, Any]]] = {}
                        for name, (prefix, trace_dir) in listeners.items():
                            count = wait_for_capture(
                                context["socket_path"],
                                trace_dir,
                                minimum=1 if prefix in active_prefixes else 0,
                                stat_prefix=prefix,
                            )
                            listener_traces = load_traces(trace_dir)
                            assert len(listener_traces) == count, (
                                f"{name} tap files did not match Envoy request counts"
                            )
                            if prefix not in active_prefixes:
                                assert count == 0, (
                                    f"inactive {name} listener captured unexpected traffic"
                                )
                            counts[name] = count
                            traces[name] = listener_traces

                        if mode == "testbox":
                            gateway_traces = traces["gateway_to_mcp"]
                            assert gateway_sidecar_credentials_stripped(
                                gateway_traces
                            ), "a caller credential reached the MCP testbox"
                            assert_backend_calls(gateway_traces)
                        else:
                            sidecar_traces = traces["gateway_to_sidecar"]
                            api_traces = traces["sidecar_to_api"]
                            assert gateway_sidecar_credentials_stripped(
                                sidecar_traces
                            ), (
                                "a caller credential reached the role-rendered Vikunja sidecar"
                            )
                            backend_auth_matches = sidecar_api_auth_matches(
                                api_traces, context["vikunja_read_token"]
                            )
                            assert backend_auth_matches, (
                                "a sidecar-to-Vikunja API request lacked its exact backend authorization"
                            )
                            assert sidecar_api_has_no_caller_material(
                                api_traces, context["caller_markers"]
                            ), "a caller header or marker reached the Vikunja API"
                            assert vikunja_request_matches(
                                sidecar_traces,
                                api_traces,
                                context["vikunja_task_id"],
                                context["vikunja_task_title"],
                            ), (
                                "same-request tap traces did not match the known Vikunja task read"
                            )

                        receipt = {
                            "acceptance_status": "pytest exit status is authoritative",
                            "agentgateway": context["images"]["agentgateway"],
                            "envoy": context["images"]["envoy"],
                            "testbox_source_commit": source_commit,
                            "host_platform": f"{platform.system().lower()}/{platform.machine().lower()}",
                            "rendered_config_sha256": context["rendered_config_sha256"],
                            "client_protocol_version": PROTOCOL_VERSION,
                            "gateway_to_mcp_request_count": counts["gateway_to_mcp"],
                            "gateway_to_sidecar_request_count": counts[
                                "gateway_to_sidecar"
                            ],
                            "sidecar_to_api_request_count": counts["sidecar_to_api"],
                        }
                        if mode == "testbox":
                            receipt["testbox"] = context["images"]["testbox"]
                        else:
                            receipt.update(
                                {
                                    "vikunja": context["images"]["vikunja"],
                                    "vikunja_mcp_sidecar": context["images"][
                                        "vikunja_mcp"
                                    ],
                                    "vikunja_mcp_package_version": "0.2.0",
                                    "vikunja_version": context["vikunja_version"],
                                }
                            )
                        print(
                            f"gateway_falsifier_identity_only={json.dumps(receipt, sort_keys=True)}"
                        )
                finally:
                    compose_run("down", "--volumes", "--remove-orphans")
        finally:
            shutil.rmtree(run_dir)
