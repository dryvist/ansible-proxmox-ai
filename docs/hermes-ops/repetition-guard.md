# Repetition guard

The default brain (the real model id in `ai_default_model`) has its own tuned
entry in the repo-root `llm-models.yml` registry carrying
`repetition_penalty: 1.05` in `extra_body`; because the router serves that real
id (no alias indirection),
requests hit the tuned entry rather than falling through to the un-tuned `*`
wildcard. If 1.05 proves insufficient the next levers are `temperature ~1.0` /
`presence_penalty 0.0` in the same `extra_body`. Incident history: Zammad
(AI/LLM Serving).
