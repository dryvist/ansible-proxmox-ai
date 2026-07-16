"""Homelab LangGraph seed: a minimal ReAct agent wired to the LiteLLM router.

`langgraph dev` serves the ``graph`` exported here (see ``langgraph.json``).
Tracing is initialised on import via ``otel_bootstrap`` so every run is exported
over OTLP to the Cribl Edge receiver, which forks to Langfuse + Splunk.

Everything configurable comes from the environment (injected by the compose
file), so this file carries no secrets and no homelab-specific literals.
"""

import datetime
import os

import otel_bootstrap  # noqa: F401  (configures OTLP tracing on import)

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


@tool
def get_current_time() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


model = ChatOpenAI(
    model=os.environ.get("LLM_MODEL", "gpt-oss-120b"),
    base_url=os.environ["OPENAI_API_BASE"],
    api_key=os.environ["OPENAI_API_KEY"],
    temperature=0,
)

graph = create_react_agent(
    model,
    tools=[get_current_time],
    prompt=(
        "You are a helpful homelab assistant running on self-hosted LangGraph. "
        "Use the available tools when they help answer the question."
    ),
)
