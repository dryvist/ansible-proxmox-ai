# LiteLLM router: direct Langfuse traces

Split out of `LLM_ROUTER_OBSERVABILITY.md` to keep both files under the
repository's per-file token budget (`.token-limits.yaml`).

When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` both resolve non-empty in
the local-llm secrets domain, the router lists the `langfuse_otel` callback next
to `otel` and its env file carries:

- both keys and `LANGFUSE_HOST`, derived from the internal subdomain
  (`llm_router_langfuse_host`);
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`, with
  `callback_settings.langfuse_otel.message_logging` false, so a trace names the
  model and carries no prompt or response bodies.

With either key empty nothing Langfuse-related renders, and neither key is part
of any converge assert. The callback exports OTLP over HTTP to the server's
`/api/public/otel` route through the OpenTelemetry packages already pinned in
`llm_router_pip_packages`; no Langfuse SDK package is installed.
