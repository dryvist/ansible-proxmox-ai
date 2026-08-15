# Memory

- **Provider:** Hindsight (knowledge-graph + multi-strategy retrieval) in
  `local_external` mode — the standalone HA Hindsight service (two stateless
  replicas behind the Traefik pool at `hindsight.<sub>`, state in the
  dedicated ai-VLAN Postgres cluster), running alongside the always-on
  built-in `MEMORY.md` / `USER.md`. Set in `defaults/main.yml`
  (`hermes_agent_memory_provider: hindsight`, `hermes_agent_memory_mode:
  local_external`, `hermes_agent_memory_api_url`). The plugin config is
  rendered to `$HERMES_HOME/hindsight/config.json` (`mode` + `api_url`).
  Rollback: set `hermes_agent_memory_mode: local_embedded` and converge — the
  embedded-daemon path (hindsight-all in the venv, extraction LLM at the
  router) is still fully wired.
- **Persistence:** memory now lives in the ai-VLAN Postgres cluster (backed
  up under the database DR standard). The rest of `HERMES_HOME`
  (`/var/lib/hermes/.hermes`) — skills, profiles, the Kanban DB, sessions,
  logs, `MEMORY.md`/`USER.md` — remains the guest's durable surface on its
  snapshotted, replicated ZFS dataset.
- **Mode matters:** Hindsight defaults to a *cloud* mode that needs an API
  key. With no key, `is_available()` returns false and every memory tool
  call warns "Memory is not available" — a repeated, useless status line.
  An explicit mode (`local_external` today) + the rendered
  `hindsight/config.json` is what makes memory actually work. Verify with a
  non-fatal `hermes memory status` probe (run in `verify.yml`).
- **Shared across profiles, by design.** Every named profile gets the same
  `hindsight/config.json` (mode + `api_url`), and neither sets `bank_id` nor
  `bank_id_template` — verified against the pinned hermes-agent's
  `plugins/memory/hindsight/__init__.py`: with `bank_id_template` unset,
  `_resolve_bank_id_template` always falls back to the static `bank_id`
  (default `"hermes"`), which every profile therefore shares. Moving a
  recurring card's `assignee` does **not** reset its memory continuity, and
  `daily-summary` (default) can still recall a moved job's findings. Memory
  is explicitly **not** part of the profile isolation boundary — do not rely
  on it to separate what one profile "knows" from another.

> If you see a runtime loop of a repeated memory status line (e.g.
> `Opening memory…Opening memory…`), that is the **brain degenerating**, not a
> memory bug — see "Repetition guard" below. The literal string is an upstream
> runtime line, not something this role emits.
