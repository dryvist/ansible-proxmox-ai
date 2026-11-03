"""The evaluation Dify resolves its model endpoint, so its compose file renders."""

from __future__ import annotations

from _eval_support import host_context, render


def test_dify_eval_model_endpoint_resolves_to_the_router() -> None:
    context = host_context("dify_docker", "dify_eval_group")
    context["group_names"] = ["dify_eval_group"]
    context["ai_subdomain"] = "ai.example.test"

    assert render("{{ dify_docker_model_endpoint_url }}", context) == "https://llm.ai.example.test/v1"
