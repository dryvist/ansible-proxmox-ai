# Model registry

One slice per serving tier. The registry is the concatenation of these files,
in the order `llm_router_model_registry` lists them
(`roles/llm_router/defaults/main/20-registry.yml`) — that order is the rendered
order, so adding a slice means adding it there too, deliberately.

Split from a single `llm-models.yml` that had reached its token budget: an
agent changing one tier should read one file, not every model in the estate.
