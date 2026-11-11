# Declared serving metrics

`tasks/publish.yml` writes one node_exporter textfile file describing a serving
guest's declared capacity: `llm_serving_declared_slots` and
`llm_serving_declared_context_tokens_per_slot`, labelled with the guest's
`llm_gpu_accelerator`, the profile and the engine.

## Installation

Nothing to install. The serving roles `llm_gpu_serving` and `vllm_serving`
include this role's tasks from their activation step.

## Usage

```yaml
- name: Publish the active profile's declared serving metrics
  ansible.builtin.include_role:
    name: llm_serving_declared_metrics
    tasks_from: publish.yml
  vars:
    llm_serving_declared_metrics_profile: "{{ llm_active_profile }}"
    llm_serving_declared_metrics_engine: "{{ llm_gpu_serving_profiles[llm_active_profile].engine }}"
    llm_serving_declared_metrics_slots: "{{ llm_gpu_serving_profiles[llm_active_profile].max_num_seqs | int }}"
    llm_serving_declared_metrics_context_tokens_per_slot: >-
      {{ llm_gpu_serving_profiles[llm_active_profile].max_model_len | int }}
```

The directory and file name are declared in `defaults/main/00-textfile.yml`.
When the directory is absent (node_exporter not installed), the write is skipped.
