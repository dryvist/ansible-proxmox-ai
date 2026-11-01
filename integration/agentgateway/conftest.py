"""Pytest lifecycle for the pinned, role-rendered gateway fixture."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from gateway_falsifier_contract import (
    CALLER_CREDENTIAL_HEADERS,
    COMPOSE_FILE,
    ENVOY_IMAGE,
    PROTOCOL_VERSION,
    REPO_ROOT,
    TESTBOX_IMAGE,
    TESTBOX_SOURCE_COMMIT,
)
from gateway_observer_helpers import (
    assert_backend_calls,
    gateway_config,
    image_identity,
    load_traces,
    observer_positive_control,
    trace_headers,
    wait_for_admin,
    wait_for_capture,
    wait_for_gateway,
)
from test_authz_render import ROLE_DEFAULTS


@pytest.fixture(scope="module")
def runtime() -> Any:
    image = str(ROLE_DEFAULTS["agentgateway_docker_image"])
    assert re.fullmatch(r".+:v1\.6\.0@sha256:[0-9a-f]{64}", image), (
        "the falsifier requires the digest-pinned agentgateway v1.6.0 role image"
    )
    source_dir = Path(os.environ.get("AGENTGATEWAY_SOURCE_DIR", REPO_ROOT / ".agentgateway"))
    source_commit = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_commit == TESTBOX_SOURCE_COMMIT, "testbox source is not the reviewed upstream commit"

    root = Path(os.environ.get("FALSIFIER_RUNTIME_DIR", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="agentgateway-falsifier-", dir=root))
    socket_dir, trace_dir = run_dir / "envoy", run_dir / "tap"
    socket_dir.mkdir(mode=0o777)
    trace_dir.mkdir(mode=0o777)
    socket_dir.chmod(0o777)
    trace_dir.chmod(0o777)
    config_path = run_dir / "config.yaml"
    rendered = gateway_config()
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
    compose = [
        "docker",
        "compose",
        "--project-name",
        f"gateway-falsifier-{os.getpid()}",
        "--file",
        str(COMPOSE_FILE),
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
        "socket_path": socket_dir / "admin.sock",
        "trace_dir": trace_dir,
    }
    stack_started = False
    test_started = False
    try:
        stack_started = True
        compose_run("up", "--detach", "--no-build", "observer", "testbox")
        wait_for_admin(context["socket_path"])
        observer_positive_control(context)

        compose_run("up", "--detach", "--no-build", "agentgateway")
        wait_for_gateway()
        context["rendered_config_sha256"] = hashlib.sha256(rendered.encode()).hexdigest()
        context["source_commit"] = source_commit
        context["images"] = {
            "agentgateway": image_identity(env, image),
            "envoy": image_identity(env, ENVOY_IMAGE),
            "testbox": image_identity(env, TESTBOX_IMAGE),
        }
        test_started = True
        yield context
    finally:
        try:
            if stack_started:
                try:
                    if test_started:
                        compose_run("stop", "--timeout", "3", "agentgateway")
                        traces_count = wait_for_capture(context["socket_path"], trace_dir)
                        traces = load_traces(trace_dir)
                        assert traces_count > 0 and len(traces) == traces_count, (
                            "no upstream MCP exchanges were captured or tap traces are incomplete"
                        )
                        for trace in traces:
                            assert CALLER_CREDENTIAL_HEADERS.isdisjoint(trace_headers(trace)), (
                                "a caller credential reached the Vikunja MCP backend"
                            )
                        assert_backend_calls(traces)
                        receipt = {
                            "acceptance_status": "pytest exit status is authoritative",
                            "agentgateway": context["images"]["agentgateway"],
                            "envoy": context["images"]["envoy"],
                            "testbox": context["images"]["testbox"],
                            "testbox_source_commit": source_commit,
                            "host_platform": f"{platform.system().lower()}/{platform.machine().lower()}",
                            "rendered_config_sha256": context["rendered_config_sha256"],
                            "client_protocol_version": PROTOCOL_VERSION,
                            "observed_http_request_count": traces_count,
                        }
                        print(f"gateway_falsifier_identity_only={json.dumps(receipt, sort_keys=True)}")
                finally:
                    compose_run("down", "--volumes", "--remove-orphans")
        finally:
            shutil.rmtree(run_dir)
