# codex_runner

Installs the OpenAI Codex CLI under a dedicated, low-privilege system user —
`codex-runner` — isolated from the `hermes` user that `hermes_agent`'s
gateway daemon runs as. Exists solely to back Hermes' escalation path: Codex
exposed as an MCP tool, reachable only through a single, narrow sudo grant.

## Why a separate user

Hermes' local terminal tool already runs shell commands as the `hermes` user
inside its own LXC — the LXC itself is the blast-radius boundary for
everything Hermes can already do. Codex's ChatGPT-OAuth credential
(`~/.codex/auth.json`) is a different kind of asset: unlike Hermes' own
secrets (Splunk token, GitHub App key, etc., all already readable by `hermes`
today), this one is deliberately kept **outside** that boundary. `hermes`
gets exactly one capability — running `codex mcp-server` as `codex-runner`,
nothing else — and never gains filesystem access to that account's home, so
the OAuth token itself is not directly readable by the agent even though the
agent can still fully use the tool.

## What it does

- Installs Node.js/npm (Debian packaged, no NodeSource repo) and a
  version-pinned `@openai/codex` (npm, global, explicit `--prefix` so the
  binary path is a known fact — see `codex_runner_codex_bin`).
- Creates the `codex-runner` system user (no login shell — it only ever runs
  `codex mcp-server`) with a `0700` home directory.
- Installs a single-command sudoers grant: `hermes` may run exactly
  `{{ codex_runner_codex_bin }} mcp-server` as `codex-runner`, `NOPASSWD`,
  nothing broader. Validated with `visudo` before install.
- Reports (non-fatally) whether Codex's auth has been bootstrapped yet.

## Manual bootstrap (required, cannot be automated)

Codex auth is an interactive ChatGPT OAuth flow — Ansible cannot complete it.
After this role converges, pick ONE:

**Option A — fresh login on the LXC:**

```bash
sudo -u codex-runner -H codex login
```

Follow the printed URL/device code with a ChatGPT Pro/Plus account.

**Option B — copy an already-authenticated session:**

If a workstation already has a working `codex` CLI login (`~/.codex/auth.json`,
`auth_mode: "chatgpt"`), copy that file to the LXC as `codex-runner`:

```bash
scp ~/.codex/auth.json <lxc-host>:/tmp/codex-auth.json
ssh <lxc-host> 'sudo install -o codex-runner -g codex-runner -m 0600 \
  -D /tmp/codex-auth.json /var/lib/codex-runner/.codex/auth.json && \
  sudo rm /tmp/codex-auth.json'
```

**Option C — seed from OpenBao (bootstrap only):**

Set `CODEX_AUTH_JSON` (the `~/.codex/auth.json` content) at `secret/ai/hermes`
in the local-llm domain. On converge the role writes it to
`~/.codex/auth.json` **create-only** — it seeds the initial login and then
never touches the file again, so Codex's guest-side refresh takes over after
first use and the live token is never clobbered by the stale snapshot. Leave
the value empty to fall back to Option A or B.

Either way, confirm with:

```bash
sudo -u codex-runner -H codex --version
sudo -u codex-runner -H codex doctor   # should report auth OK
```

Until one of these is done, Hermes' `codex` MCP tool is registered but every
call to it errors — the daemon itself is unaffected (see `hermes_agent`'s
README, "Escalation (Codex via MCP)").

## Key variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `codex_runner_user` | `codex-runner` | isolated system user |
| `codex_runner_home` | `/var/lib/codex-runner` | `0700` home (holds `~/.codex/auth.json`) |
| `codex_runner_codex_version` | `0.130.0` | pinned npm version floor |
| `codex_runner_npm_prefix` | `/usr/local` | forces a known global-install path |
| `codex_runner_sudo_grantee` | `hermes` | the only user allowed to invoke Codex |
| `codex_runner_mcp_command` | `{{ codex_runner_codex_bin }} mcp-server` | the exact, single command the sudo grant covers |
| `codex_runner_auth_json` | `""` | optional `auth.json` bootstrap snapshot (Bao-first, env fallback); create-only |

## Group / invocation

Deployed to `hermes_agent_group` (the same host as `hermes_agent`), always
run *before* `hermes_agent` in `playbooks/site.yml` — Hermes' config
references this role's sudo grant, so the grant must already exist.

## Not yet live-validated

Verify on first converge: (a) Debian's packaged `nodejs`/`npm` version is new
enough for the pinned Codex floor — if not, this is a documented follow-up
(pin a newer Node via a different source), not silently worked around here;
(b) `codex mcp-server`'s actual tool surface/behavior once authenticated.
