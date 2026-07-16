# agent_exec

Plain-LXC **Python runtime** for running [CrewAI](https://docs.crewai.com/) +
[LangChain](https://python.langchain.com/) agent code, with
[OpenLLMetry](https://github.com/traceloop/openllmetry) (OTLP/HTTP) tracing. No
Docker — just a venv and a systemd service.

This role ships the **platform** (runtime user, venv, instrumentation, unit) plus
a working **autonomy seed** in `main.py` (deployed once, `force: false`). The seed
runs a tiny traced crew against both model tiers each interval; extend or replace
it with your own agents — your edits survive re-converges.

## What it does

- Installs `python3` + `git` and creates a system user `agent-exec`
  (home `/var/lib/agent-exec`).
- Builds a venv at `/opt/agent-exec-venv` with `crewai`, `crewai-tools`,
  `langchain`, and the OpenTelemetry SDK + OpenLLMetry CrewAI/LangChain
  instrumentors.
- Deploys `otel_bootstrap.py` (Ansible-managed) — import it first from your
  entrypoint to wire spans to the OTLP/HTTP collector.
- Runs `main.py` under systemd (`Restart=on-failure`) with the `OTEL_*` and
  two-tier model-endpoint env preset. The seeded `main.py` loops every
  `agent_exec_interval_seconds`, running a small traced crew against the large
  tier (real work) and the light tier (soak path); a failed run is
  logged and the loop continues.

## Your agent code

`/var/lib/agent-exec/main.py` (deployed `force: false`, never clobbered) ships a
working seed. Extend or replace it with your CrewAI/LangChain agents, keeping
`import otel_bootstrap` first so runs are traced, then
`sudo systemctl restart agent-exec`.

## Installation

This role ships with the repo; no separate install step and no collection
dependencies (plain LXC, no `community.docker`). Hosts follow the standard
selection: the `agent-exec`-tagged hosts from the terraform inventory are grouped
into `agent_exec_group` by `inventory/load_tofu.yml`, and the matching play in
`playbooks/site.yml` runs this role against them.

## Usage

Run via the standard site playbook (tag `agent_exec`):

```bash
ansible-playbook playbooks/site.yml --tags agent_exec
```

Or include the role directly against the group:

```yaml
- name: Provision the agent-exec runtime
  hosts: agent_exec_group
  become: true
  roles:
    - role: agent_exec
```

## Key variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `agent_exec_home` | `/var/lib/agent-exec` | runtime user home + working dir |
| `agent_exec_venv` | `/opt/agent-exec-venv` | Python venv |
| `agent_exec_pip_packages` | runtime + instrumentation | venv package set |
| `agent_exec_interval_seconds` | `900` | seconds between autonomy-seed cycles |
| `agent_exec_model_large` | `openai/default` | large-tier model name (LiteLLM form) |
| `agent_exec_model_light` | `openai/hermes4` | light-tier model alias (via the router) |
| `agent_exec_api_key` | `sk-noauth` | OpenAI-compatible key (runners ignore it) |

`ai_orchestration_otel_endpoint` (OTLP collector) and the two model base URLs
(`ai_orchestration_model_large_base_url`, `ai_orchestration_ollama_base_url`) are
group vars defined at the inventory level — this role consumes them.
