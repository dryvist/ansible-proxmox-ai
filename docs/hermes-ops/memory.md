# Memory

- **Provider selection:** Hermes uses Hindsight (knowledge-graph + multi-strategy
  retrieval) in `local_external` mode — the standalone HA Hindsight service (two
  stateless replicas behind the Traefik pool at `hindsight.<sub>`, state in the
  dedicated ai-VLAN Postgres cluster) — when `hindsight_service_enabled` is true.
  Otherwise the fail-closed selection is Hermes builtin memory. Both run
  alongside the always-on `MEMORY.md` / `USER.md`. The provider is selected in
  `defaults/main/70-memory.yml`; Hindsight mode and URL are configured by
  `hermes_agent_memory_mode: local_external` and
  `hermes_agent_memory_api_url`. The plugin config is rendered to
  `$HERMES_HOME/hindsight/config.json` (`mode` + `api_url`). To use the embedded
  rollback path, set `hermes_agent_memory_mode: local_embedded` and converge;
  the embedded daemon remains wired.
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
  non-fatal `hermes memory status` probe (run in `verify.yml`). When builtin
  memory is selected, that command reports the configured core provider and
  does not prove external Hindsight health; the watchdog omits Hindsight probes.
- **Managed client and readiness gate:** when Hindsight is selected, the role installs its exact
  `hindsight-client` pin with the installer's uv binary into Hermes's own venv;
  it never relies on the plugin's lazy pip/ensurepip fallback. The post-converge
  gate verifies that distribution and performs a read-only recall through the
  rendered config. An empty recall result is valid; a failed request is not.
- **Separate agents; shared profiles.** Every Hermes agent uses the same mode
  and Hindsight API endpoint, but its static `bank_id` derives from
  `hermes_agent_id`. Different agents therefore retain and recall distinct
  memory banks. Named profiles within one agent receive the same config and
  share that agent's bank because `bank_id_template` is deliberately unset.
  Moving a recurring card's assignee between profiles does not reset its
  memory continuity. Memory is not a profile isolation boundary. A profile
  entry can opt out with `memory_bank_id`; see the operating-profiles doc for
  the `splunk-admin` case. `memory_enabled: false` turns memory off for one
  profile (`eval`).

> If you see a runtime loop of a repeated memory status line (e.g.
> `Opening memory…Opening memory…`), that is the **brain degenerating**, not a
> memory bug — see "Repetition guard" below. The literal string is an upstream
> runtime line, not something this role emits.
