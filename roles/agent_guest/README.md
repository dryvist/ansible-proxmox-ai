# agent_guest

Converges a pooled Debian LXC (`ai-runner-pool-NN`, `ai-proxied` firewall
profile) into an autonomous coding-agent guest: it pulls jobs off the Vikunja
queue, runs one headless CLI agent per job, and opens a PR with the result.

**Successor to [`ai_runner`](../ai_runner/README.md).** Same queue, same safety
model — the LXC + firewall boundary is the control, not the agent, which runs in
permission-skipping mode by design. The legacy role stays in-tree and keeps
converging the three `ai-runner` guests until this one carries load on the
canary; retiring it is a follow-up.

## What it adds over ai_runner

| Area | ai_runner | agent_guest |
| --- | --- | --- |
| Agents | claude, codex | claude, codex, **gemini** |
| Job isolation | one long-lived worker, in-process timeout | **one systemd unit per job**: `RuntimeMaxSec`, `PrivateTmp`, killed with its cgroup |
| Credentials | API keys rendered into a 0600 env file | **`bao agent` process-supervisor** renders them into the job's environment only |
| Output | comment on the task | **clone → branch → gitleaks → push → PR**, plus the comment |
| Policy | none | **nix-ai autonomous deny mirror** for all three CLIs |
| Telemetry | none | **OTel traces** + per-guest Cribl Edge transcript shipping |
| Teardown | none | **pool-return drain gate** |

## Installation

Applied by `playbooks/site.yml` to `ai_agent_pool_group` — the group
`inventory/load_tofu.yml` builds from the tofu **`ai-proxied`** tag. Note that
is deliberately not the broader `ai-runner` tag: that one also selects the three
legacy guests `ai_runner` owns, and two roles must never converge one guest.

```bash
doppler run -- ansible-playbook -i inventory/hosts.yml playbooks/site.yml \
  --tags agent_guest --limit ai_agent_pool_group,localhost --forks 25
```

## Usage

Queue a job by creating a task in the **AI Jobs** Vikunja project carrying this
guest's profile label (`agent_guest_profile_label`, default
`profile:ai-proxied`). The description is optional front-matter plus the prompt:

```text
---
agent: codex          # claude | codex | gemini  (default claude)
model: gpt-5.2        # optional
repo: dryvist/foo     # optional — omit for a no-checkout job
base: develop         # optional
---
Summarize the open PRs and comment the top risk on each.
```

With `repo`, the job runs in a throwaway clone and ends in a PR. Without it, it
runs in the agent's home and only the output is reported — ai_runner's
behaviour, so existing queue entries keep working.

The poller claims the task (assigns it to itself) and starts
`agent-task@<task-id>.service`. Watch one:

```bash
journalctl -fu agent-task@<task-id>.service
```

## Queue mechanics

The poll timer runs `agent-task --poll` as root; its only privileged action is
`systemctl start`. Claiming is assign-then-work, inherited from ai_runner: the
race window is small (claim precedes dispatch, and systemd refuses a second
`agent-task@<same-id>`), but two guests sharing one profile label could still
double-claim. Give each guest its own label if a label ever fans out.

**Alternative considered — an S3/RustFS queue** with an atomic claim
(conditional `PutObject` of a lease key with `If-None-Match: *`, so the store
412s the second writer). Genuinely race-free, but it trades away the
human-visible queue and the existing worker for a problem one label-per-guest
already solves. Revisit only if the queue needs to fan out beyond what labels
can express.

## Autonomous config mirror

`agent_guest_residual_deny` mirrors [`dryvist/nix-ai`](https://github.com/dryvist/nix-ai)
`profiles.autonomous.residualDeny` — that repo is the source of truth.
Regenerate after upstream changes:

```sh
nix eval github:dryvist/nix-ai#lib.profiles.autonomous.residualDeny --json
```

The mirror is the **one source list**, not nix-ai's five rendered files: the
templates reproduce each tool's native format from it (Claude `Bash(<prefix> *)`
entries, Codex `prefix_rule` tokens, Gemini Policy-Engine `commandPrefix`
rules), byte-equal to `nix eval …#lib.renderAutonomous`.

Two launch flags are load-bearing and asserted by the runner's self-check:

- **claude gets `-p` without `--bare`** — bare mode ignores `CLAUDE_CODE_OAUTH_TOKEN`.
- **gemini gets `--approval-mode yolo`** — gemini-cli strips the equivalent
  settings key, so the settings block is render parity only.

## Credentials

`agent-task@.service` never runs the job script directly. `ExecStart` is
`bao agent -config=…`, which authenticates with the guest's AppRole, renders
`GITHUB_TOKEN` (from the **github secrets engine**, never a static PAT) and the
Claude subscription token into the child environment, then execs the runner. The
task id is not baked into the HCL: the unit passes `AGENT_TASK_ID=%i` and the
runner reads it when no id argument follows `--task`.

AppRole credentials are **seeded out of band** (0640 `root:agent`); this role
creates the directory and never writes a secret into it. `bao agent` runs as the
`agent` user, so the guest's OpenBao identity is the agent user's — scope the
AppRole accordingly.

**`ANTHROPIC_API_KEY` is deliberately absent everywhere.** ai_runner rendered it
into its worker env; that outranks `CLAUDE_CODE_OAUTH_TOKEN` in claude's auth
precedence and silently moves billing off the subscription. Never reintroduce it.

Codex (`~/.codex/auth.json`) and Gemini (`~/.gemini/oauth_creds.json`) auth files
are seeded out of band beside the AppRole credentials — they self-refresh in
place, and `bao agent` env_template renders environment variables, not files.

## Transcript shipping

A standalone Cribl Edge tails `/home/agent` for the three CLIs' transcripts and
ships them by `tcpjson` to the HAProxy-fronted Cribl Stream per-CLI frontends.
Ports come from the tofu `ai_log_routing` constant (`claude_code` / `codex_cli` /
`agy_cli`) — never a literal. A 4 MiB newline breaker handles the oversized
transcript lines. The journal is not duplicated here; the estate's syslog
forwarder already ships it.

## Guest rebuild / pool return

`tasks/pool_return.yml`, tagged `never` so it only runs when asked for:

```sh
ansible-playbook playbooks/site.yml --tags agent_guest_pool_return --limit <guest>,localhost
```

The order is load-bearing: (1) wait for the Cribl Edge persistent queue to drain
to zero files — a clean `systemctl stop` is **not** delivery proof, the queue
survives restarts by design, and the wait fails loud rather than recycling a
guest with an undeliverable backlog; (2) stop Edge; (3) remove credentials;
(4) wipe the workspace. Reversed order strands undeliverable telemetry.
