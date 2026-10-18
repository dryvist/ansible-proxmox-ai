# Strata serving

This role installs the pinned Strata release for the registered
`flash-next-iq3-s` artifact. It reads both GGUF shards from the read-only
model-store mount, writes generated data to the writable model-cache mount,
and serves the OpenAI-compatible API and Prometheus metrics.

Set `strata_serving_parallel` to `1`, `4`, or `8` for each campaign. This
slot count maps to the engine's batch setting, and the generated config
includes Qwen's MTP path. The service sets `STRATA_REQUEST_LINES=1` and does
not configure an API key.
