"""Dify images follow 1.19.0 Compose while retaining the deployed Weaviate pin."""

from _defaults import load_defaults


def test_dify_119_images_follow_upstream_except_deployed_weaviate():
    defaults = load_defaults()

    assert defaults["dify_docker_api_image"] == "langgenius/dify-api:1.20.0"
    assert defaults["dify_docker_web_image"] == "langgenius/dify-web:1.20.0"
    assert defaults["dify_docker_plugin_daemon_image"] == "langgenius/dify-plugin-daemon:0.6.15-local"
    # Upstream 1.19.0 still pins the code sandbox at 0.2.15.
    assert defaults["dify_docker_sandbox_image"] == "langgenius/dify-sandbox:0.2.15"
    assert defaults["dify_docker_weaviate_image"] == "cr.weaviate.io/semitechnologies/weaviate:1.40.3"
    assert defaults["dify_docker_agent_backend_image"] == "langgenius/dify-agent-backend:1.20.0"
    assert defaults["dify_docker_local_sandbox_image"] == "langgenius/dify-agent-local-sandbox:1.20.0"


def test_existing_infrastructure_image_pins_are_unchanged():
    defaults = load_defaults()

    assert defaults["dify_docker_postgres_image"] == (
        "postgres:15-alpine@sha256:e18480a22d6e48f4d816a6696769b1ec09ac73a824c5649e49c7d8b684913f3d"
    )
    assert defaults["dify_docker_redis_image"] == (
        "redis:8-alpine@sha256:5af80107c1eeecb484a2269b80a26d12bfa6521787da4663bd9f19f60a163483"
    )
    assert defaults["dify_docker_nginx_image"] == (
        "nginx:1.31-alpine@sha256:a3554c0d0b48177b1413c26e01f695334428020d9df01243fbae3e11cfd17417"
    )
    assert defaults["dify_docker_ssrf_proxy_image"] == (
        "ubuntu/squid:latest@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029"
    )
