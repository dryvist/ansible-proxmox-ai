# Declared serving metrics

`tasks/publish.yml` writes one node_exporter textfile file describing a serving
guest's declared capacity: `llm_serving_declared_slots` (the profile's
`max_num_seqs`) and `llm_serving_declared_context_tokens_per_slot` (the profile's
`max_model_len`), labelled with the guest's `llm_gpu_accelerator`, the profile and
the engine.

## Installation

Nothing to install. The serving roles `llm_gpu_serving`, `vllm_serving`,
`llamacpp_serving`, and `sglang_serving` include this role's tasks from their
activation step.

## Usage

Each caller passes three parameters and nothing else:

```yaml
- name: Publish the active profile's declared serving metrics
  ansible.builtin.include_role:
    name: llm_serving_declared_metrics
    tasks_from: publish.yml
  vars:
    llm_serving_declared_metrics_profile: "{{ llm_active_profile }}"
    llm_serving_declared_metrics_profiles: "{{ llm_gpu_serving_profiles }}"
    llm_serving_declared_metrics_engine: "{{ llm_gpu_serving_profiles[llm_active_profile].engine }}"
```

The directory and file name are declared in `defaults/main/00-textfile.yml`.
The role creates the textfile collector directory when it is missing. Its path
must match the directory configured for node_exporter to collect. If directory
creation fails, the role fails with the path and the
`llm_serving_declared_metrics_textfile_collector_dir` variable name.
