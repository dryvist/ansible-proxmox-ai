"""Dify images follow 1.18.0 Compose while retaining the deployed Weaviate pin."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"


def test_dify_118_images_follow_upstream_except_deployed_weaviate():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))

    assert defaults["dify_docker_api_image"] == "langgenius/dify-api:1.18.0"
    assert defaults["dify_docker_web_image"] == "langgenius/dify-web:1.18.0"
    assert defaults["dify_docker_plugin_daemon_image"] == "langgenius/dify-plugin-daemon:0.6.10-local"
    assert defaults["dify_docker_sandbox_image"] == "langgenius/dify-sandbox:0.2.15"
    assert defaults["dify_docker_weaviate_image"] == "cr.weaviate.io/semitechnologies/weaviate:1.40.1"
    assert defaults["dify_docker_agent_backend_image"] == "langgenius/dify-agent-backend:1.18.0"
    assert defaults["dify_docker_local_sandbox_image"] == "langgenius/dify-agent-local-sandbox:1.18.0"


def test_existing_infrastructure_image_pins_are_unchanged():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))

    assert defaults["dify_docker_postgres_image"] == (
        "postgres:15-alpine@sha256:a8e042598834300dfaa17a54f695b87b7f25d3490433bbbe2ef3bffbb9747f24"
    )
    assert defaults["dify_docker_redis_image"] == (
        "redis:8-alpine@sha256:0bf6577d7774e592b29d093e3ac9390eb91e13d72c21e506afce736de5e0eeb0"
    )
    assert defaults["dify_docker_nginx_image"] == (
        "nginx:1.31-alpine@sha256:a3554c0d0b48177b1413c26e01f695334428020d9df01243fbae3e11cfd17417"
    )
    assert defaults["dify_docker_ssrf_proxy_image"] == (
        "ubuntu/squid:latest@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029"
    )
