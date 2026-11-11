# Review verdict — pull request #2354

Reviewed head: `f421d4ed50f5d0ca4931f353bac5eb1dde4a1d09`
Diff base at review time: `a9f8590142c68c9bca99ecefac7c52b4e34f85e3` (merge-base with develop)

## Scope of the change

Two task files publish the active serving profile's declared slot count and
per-slot context as node_exporter textfile metrics:
`roles/vllm_serving/tasks/publish-declared-metrics.yml` and
`roles/llm_gpu_serving/tasks/publish-declared-metrics.yml`, included from
`roles/vllm_serving/tasks/activate.yml:66-68` and
`roles/llm_gpu_serving/tasks/main.yml:343-345` after the handlers flush and
guarded against render-only runs. Each role declares the collector directory
as a default (`roles/vllm_serving/defaults/main/20-metrics.yml:7`,
`roles/llm_gpu_serving/defaults/main/00-core.yml:99`). A render test pins the
output (`tests/llm_gpu_serving/test_declared_metrics.py`).

## Correctness

- The published values match the real start commands. For llama.cpp,
  `roles/llm_gpu_serving/templates/llama-cpp-serve-args.j2:14-15` passes
  `--ctx-size` as `max_model_len x max_num_seqs` and `--parallel
  max_num_seqs`, so per-slot context is `max_model_len`; for vLLM,
  `roles/vllm_serving/templates/vllm-serving.service.j2:31-32` and
  `roles/llm_gpu_serving/templates/llm-gpu-serving.service.j2:32-33` pass
  `--max-model-len` and `--max-num-seqs` directly. Both task files read the
  profile fields rather than restating literals.
- All referenced variables resolve. Every entry in
  `roles/llm_gpu_serving/defaults/main/10-profiles.yml` carries `engine`,
  `max_model_len`, and `max_num_seqs`; `vllm_serving_profiles` is the merged
  map (`roles/vllm_serving/defaults/main/10-profiles.yml:303`) and
  `vllm_serving_active_profile` always resolves through the static per-engine
  map (`inventory/group_vars/all/00-core.yml:47-50`).
- The accelerator label rule reproduces the router's rule
  (`roles/llm_router/defaults/main/21-gpu-profiles.yml:34-38`): the router
  keys members by inventory hostname
  (`roles/llm_router/defaults/main/20-registry.yml:45-58`), and the two label
  values are loaded from the files that declare them, not retyped.
- Cross-role `include_vars` follows existing precedent
  (`roles/llm_gpu_serving/tasks/load-registry.yml:19-22`). Only the literal
  label scalars are consumed from the loaded files, so the lazy expressions
  elsewhere in those files never evaluate.
- The stat gate skips without the collector directory instead of failing the
  converge; the copy is mode 0644 with atomic staging; render-only runs skip
  the include.
- Checks run during this review: `python3 -m pytest tests/llm_gpu_serving/`
  — 14 passed; `python3 -m pytest tests/llm_router/test_registry_retype_scan.py`
  — 10 passed; `ansible-lint --offline` on every touched file — passed under
  the production profile. Test collection failures elsewhere in this sandbox
  are a missing Python module in the review environment, unrelated to this
  change.

## Findings

1. `roles/llm_gpu_serving/tasks/publish-declared-metrics.yml:48` publishes
   only the active profile, but this role also starts and health-checks
   co-resident units (`roles/llm_gpu_serving/tasks/main.yml:351-364`), and
   co-resident-only profiles are enabled in its defaults
   (`roles/llm_gpu_serving/defaults/main/10-profiles.yml`). The router's
   accelerator map covers active plus co-resident profiles
   (`roles/llm_router/defaults/main/21-gpu-profiles.yml:34-36`), so the
   declared capacity of co-resident units is absent from the metric file.
   Published rows stay correct per profile label, and extending the publish
   to `[llm_active_profile] + llm_gpu_serving_co_resident_profiles` is
   additive. The other role is unaffected: its activation path starts only
   the active unit (`roles/vllm_serving/tasks/activate.yml:59-61`).
2. The lane-accelerator selection rule is now encoded in three places: the
   router's computed map
   (`roles/llm_router/defaults/main/21-gpu-profiles.yml:34-38`) plus inline
   restatements (`roles/llm_gpu_serving/tasks/publish-declared-metrics.yml:51-54`,
   `roles/vllm_serving/tasks/publish-declared-metrics.yml:46-50`). The label
   values are single-sourced through the loaded router defaults, so no value
   is retyped, but the membership rule itself is triplicated: a taxonomy
   change needs three synchronized edits, and a drifted serving-side rule
   would mislabel the metric relative to the routing map. One declared source
   feeding every consumer — for example an inventory-declared accelerator
   consumed by both the router map and these tasks, or one shared
   parameterized task file on the pattern of
   `playbooks/tasks/pool_member_gate.yml`, which three plays already share —
   would remove the duplication.
3. Minor: the two task files duplicate the whole mechanism (stat gate, copy,
   vars), differing only in variable prefixes, and repeat the metric file
   name and metric names as literals across both roles and the test. The
   remedy shape is the same as finding 2.

## Review-gate answers

(a) Second copy of an existing mechanism: no for the publishing mechanism
itself — no textfile-collector or declared-capacity metric existed in this
repository before this change, and the metric names appear nowhere else.
Partially yes for the accelerator labeling: the decision rule already exists
as the router's computed map, and this change re-derives it inline in two
roles instead of consuming a declared output (finding 2).

(b) Hard-coding for one consumer: no. The collector directory, file mode, and
metric names are declared defaults or contract constants; no host name,
owner, path, model id, or limit is hard-coded; the selection rule keys on a
shared inventory group and selects values loaded from the files that declare
them. Finding 2 records the residual duplication of the rule itself.

VERDICT: MERGE
