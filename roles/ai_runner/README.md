# ai_runner

Configures a headless AI job runner: a dedicated low-privilege system user that
runs the Claude Code and Codex CLIs in permission-skipping mode, driven by a
systemd worker polling a Vikunja queue.

This is the guest-side of the headless AI execution plane (WS7A). Guests are
provisioned by `tofu-proxmox` (the `ai-runner` tag creates the LXC; the
`ai-github` tag applies the firewall egress profile). Safety is enforced at the
**container/firewall boundary**, not the agent level — the worker runs agents
with `--dangerously-skip-permissions` by design, so a runner must only ever live
behind a restrictive egress profile.

## What it does

1. Installs packaged `nodejs`/`npm`/`python3`, then the pinned
   `@anthropic-ai/claude-code` and `@openai/codex` CLIs (global npm, explicit
   prefix — same idiom as `codex_runner`).
2. Creates the `ai-runner` system user with a locked (0700) home.
3. Renders the worker script (`ai-job-worker.py`, stdlib only), its env file
   (0600, holds the API tokens), and a systemd unit; enables the service.

## Installation

Applied by `playbooks/site.yml` to the `ai_runner_group` — the inventory group
`inventory/load_tofu.yml` builds from the tofu `ai-runner` tag. Converge scoped:

```bash
doppler run -- ansible-playbook -i inventory/hosts.yml playbooks/site.yml \
  --tags ai_runner --limit ai_runner_group,localhost --forks 25
```

The OpenBao pre-fetch play (tagged `always`) supplies the secrets, so no
`--tags openbao_secrets,ai_runner` pairing is needed.

## Usage

The worker polls `AI_JOBS_PROJECT` ("AI Jobs") every `POLL_INTERVAL_SECONDS`.
For each undone, unassigned task carrying this guest's `AI_RUNNER_PROFILE_LABEL`
(e.g. `profile:ai-github`) it:

- assigns the task to itself (claim),
- reads `agent` / `model` from the description front-matter (a leading
  `---`-delimited `key: value` block), the rest is the prompt,
- runs `claude -p <prompt> --dangerously-skip-permissions --model <model>` (or
  `codex exec` for `agent: codex`) with a hard `JOB_TIMEOUT_SECONDS` ceiling,
- posts the output as a task comment, marks the task done, pushes an ntfy alert.

Queue a job by creating a task in the AI Jobs project with a description like:

```text
---
agent: claude
model: claude-sonnet-5
---
Summarize the open PRs in dryvist/homelab and comment the top risk on each.
```

## Secrets

Injection-agnostic: role defaults read `lookup('env', ...)`; the bao-first
override lives in `inventory/group_vars/ai_runner_group.yml` (the `ai-runner`
OpenBao domain — `VIKUNJA_API_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
The model-provider keys are third-party static secrets, so KV is the correct
tier (WS7A spec). Empty tokens leave the worker idle rather than failing.

## Key variables (`defaults/main.yml`)

| Variable | Purpose |
| --- | --- |
| `ai_runner_claude_code_version` / `ai_runner_codex_version` | pinned CLI floors |
| `ai_runner_vikunja_url` / `ai_runner_vikunja_project` | dispatch queue |
| `ai_runner_profile_label` | which jobs this guest claims |
| `ai_runner_poll_interval_seconds` / `ai_runner_job_timeout_seconds` | poll + kill timings |
| `ai_runner_ntfy_topic` | result-push topic |

## Not yet live-validated

- The exact Vikunja v1 endpoint bodies (assignee/comment) are validated on
  first converge — this phase ships no converge.
- The pinned `claude-code` version floor is a placeholder; reconcile against the
  current published CLI before the first real converge.
- Per-guest CIDR-bound OpenBao AppRole + policy for the `ai-runner` domain must
  be provisioned in `ansible-proxmox-apps` `roles/openbao` (the server side);
  this role only consumes it.
