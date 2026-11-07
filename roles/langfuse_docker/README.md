---
type: reference
title: Native Langfuse evaluation
description: Public API inputs for datasets, local model prices, and sampled judges.
---

## Native evaluation

The role reconciles Langfuse datasets, model prices, LLM-as-a-judge evaluators,
and sampling rules through the public API. Each write follows a read.
`langfuse_docker_native_evaluation_enabled` defaults to `true`; check mode
omits API reconciliation. The Stage 0 TypeScript evaluator is a removal candidate;
its existing opt-in flags remain independent.

### Inputs

- `langfuse_docker_datasets`: dataset objects accepted by the datasets API. Defaults
  create the configured Stage 0 train and holdout datasets. Existing datasets
  and their items are retained.
- `langfuse_model_prices`: `{model_pattern, unit, input_price, output_price}`
  entries. Defaults derive local client model names from the router registry.
  `langfuse_docker_local_unit_price` defaults to `0` USD per token. Changed
  prices create dated definitions; previous definitions remain available.
- `langfuse_evaluators`: defaults to `[]`. Each entry supplies `name`,
  `prompt_name`, `model`, and `target: {agent, tier}`. `prompt_label` defaults
  to `production`; `sampling_rate` defaults to `0.1`.
- `output_schema`: optional evaluator JSON Schema with numeric `score`
  (`minimum`, `maximum`) and string `reasoning` properties. The default range
  is `0` through `1`; Langfuse returns native score reasoning.

`model` selects a local router model or role name. The existing
`litellm-router` connection supplies its endpoint and scoped key.
Rules match both `agent:<agent>` and `tier:<tier>` trace tags.

Judge prompts are released from `ai-llm-prompts` as `judge-<agent>` prompts.
The role fetches their text or chat content from Langfuse by name and label.
Variables map to the native sources `input`, `output`, `metadata`, `tool_calls`,
`expected_output`, and `experiment_item_metadata`. Unsupported variables,
duplicate resource names, missing prompts, and API errors fail reconciliation.

### Verification

Run `pytest tests/langfuse_docker/ -q` and `ansible-lint roles/langfuse_docker`.
The native API tests use a temporary loopback fixture and exercise repeat runs,
configuration updates, pagination, and failure responses.

### API reference

- [Public API schema](https://github.com/langfuse/langfuse/blob/v4.70.0/web/public/generated/api/openapi.yml)
- [Stable evaluator API](https://langfuse.com/changelog/2026-08-27-stable-evaluator-api)
