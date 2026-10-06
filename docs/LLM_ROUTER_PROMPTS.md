# Router prompt ownership

Use LiteLLM's built-in prompt management for plain-text prompts owned by the
router runtime. Keep `ai-llm-prompts` canonical for OKF-rendered, templated,
or multi-consumer prompt assets. Remove a duplicate only after every consumer
uses its canonical owner. The router currently declares no LiteLLM prompt IDs
or prompt bodies, so there is no router duplicate to delete.
