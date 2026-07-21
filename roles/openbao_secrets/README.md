# openbao_secrets

A controller-side **pre-play** that logs in to OpenBao **once per
resource-domain identity** (each with its own least-privilege AppRole) and
reads that domain's KV subtree into a per-domain fact,
`bao_<domain>_secrets` (hyphens become underscores, e.g. `local-cloud` ->
`bao_local_cloud_secrets`). Consumer roles then resolve their secrets
**bao-first with an env fallback**:

```yaml
some_secret: "{{ bao_local_llm_secrets.SOME_SECRET | default(lookup('env', 'SOME_SECRET'), true) }}"
```

This is a copy of the same-named role in `ansible-proxmox-apps`, trimmed to
the one domain this repo consumes (`local-llm`). The generic machinery
(failover probe, per-domain AppRole login, flat merge) is identical.

It runs **once** on the controller (`delegate_to: localhost`, `run_once`),
looping `openbao_secrets_domains` and leaving each domain's merged result in
`openbao_secrets_by_domain[<domain>]` on the controller; the calling playbook
then copies each domain's dict into its own shared, un-prefixed
`bao_<domain>_secrets` fact per host (the publish lives at the playbook
layer — like `tofu_data` in `load_tofu.yml` — so the role's own facts stay
role-prefixed). Each domain **skips cleanly** when its own role_id/secret_id
envs are unset, so an operator can migrate one domain at a time onto OpenBao
while the rest keep resolving from env/SOPS (the `group_vars/all.yml` `{}`
defaults guarantee the fallback resolves for every domain).

## Alignment with `roles/openbao` (RBAC)

The layout is aligned to what `roles/openbao` bootstraps — do not invent paths
a policy can't read:

- **KV mount**: `secret` (KV v2).
- **One AppRole per domain**, not one shared identity — a domain's
  credentials only ever unlock that domain's own KV subtree. See
  `tofu-proxmox` `docs/SECRETS_HIERARCHY.md` for the full RBAC table.
- **Seeding**: `roles/openbao` seeds **no** values; it creates only the mount,
  policies, and AppRoles. So a domain's paths yield keys only once a writer
  populates them; until then every read is empty and the env fallback wins.

## Installation

The role lives in this repository under `roles/openbao_secrets/`. Reference
it from a pre-fetch play — no Galaxy install needed:

```yaml
- hosts: <union of every domain's consumer groups>
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: openbao_secrets
```

## Usage

Set `BAO_ADDR` plus each domain's `<DOMAIN>_VAULT_ROLE_ID` / `_SECRET_ID`
(see [Inputs (env)](#inputs-env)), then run through the normal wrapper:

```sh
doppler run -- ansible-playbook playbooks/site.yml --tags openbao_secrets
```

Any domain whose credentials aren't set simply falls back to env/SOPS for its
consumer roles — see [Wiring](#wiring) for how the pre-fetch play publishes
each domain's fact.

## Client failover

Before fetching, the controller probes an ordered list of endpoints and pins the
first that answers a health check unsealed (standbyok, since a standby forwards
to the active peer so any node can serve the read). It tries `BAO_ADDR` (the
Traefik ingress VIP name, made HA by the `keepalived` role) first, then each
`openbao`-tagged container's own per-node endpoint derived from the tofu
inventory (no literal address), so failover holds even if both ingress instances
are unreachable. If none answers, the role skips to the env/SOPS fallback for
this run rather than hard-failing every converge. This is client failover only;
server-side quorum HA needs a fourth node and is out of scope.

## Domains fetched (`openbao_secrets_domains`)

| Domain | AppRole env vars | KV paths | Consumers |
| --- | --- | --- | --- |
| `ai-public` | `AI_PUBLIC_VAULT_ROLE_ID` / `_SECRET_ID` | `ai/public/brain` (non-secret) | `ai_default_model` + brain-sync timers (below) |
| `local-llm` | `LOCAL_LLM_VAULT_ROLE_ID` / `_SECRET_ID` | `ai/*` (see the defaults for the path list) | every AI role in this repo |

Other resource domains (observability, media, apps, ...) are fetched by the
`ansible-proxmox-apps` copy of this role for the roles that live there.

`local-llm` replaces the old `ai-readonly`-backed `bao_ai_secrets` for the LLM
**serving stack** — `ai-readonly`/`ai-elevated` are reserved for AI AGENT
identities (Claude/codex-style agents), not the serving stack itself; keeping
them separate means an agent's credentials can never be used to read the
serving stack's own secrets, and vice versa.

`ai-public` is deliberately its OWN domain rather than another `local-llm`
path: it is the one domain whose role_id/secret_id also gets copied onto
consumer guests (see [Wiring](#wiring)) for a runtime poll, so its policy
must stay narrowly read-only on non-secret data — never local-llm's broader
`ai/*` grant, which a compromised guest could otherwise ride to every other
`ai/*` secret. **Needs a companion AppRole + read-only policy on
`secret/data/ai/public/*` in `ansible-proxmox-apps`' `roles/openbao`
(`openbao_approles`/`openbao_policies`) before it resolves live** — until
then it skips cleanly like any other unconfigured domain, and
`ai_default_model` falls back to its static literal.

All readable path keys for a domain are merged flat into that domain's
`bao_<domain>_secrets`, keyed by the field name, so a consumer default reads
`bao_<domain>_secrets.<FIELD_NAME>`.

## Inputs (env)

| Env var | Purpose |
| --- | --- |
| `BAO_ADDR` | OpenBao ingress URL (`https://openbao.<subdomain>`). Unset ⇒ skip everything. |
| `<DOMAIN>_VAULT_ROLE_ID` / `_SECRET_ID` | That domain's own AppRole credentials. Unset ⇒ skip just that domain. |

On macOS these arrive in the ambient environment via `doppler run`; on Linux
guests, from a root-only systemd credential / `0600` EnvironmentFile. See
`tofu-proxmox` `docs/SECRETS_HIERARCHY.md`.

## Wiring

Runs as a pre-fetch play in `playbooks/site.yml`, against the union of every
domain's consumer groups, before any of those roles evaluate their secret
defaults. One execution fetches ALL configured domains (regardless of which
specific groups are in the play's host list) — cheap and avoids redundant
logins from being split across multiple domain-scoped plays.

## Dependencies

- `community.hashi_vault` (added to `requirements.yml`) and its Python `hvac`
  dependency in the controller environment (provided by the Nix dev shell —
  `nix-devenv#ansible-apps`).

## Not yet live-validated

Verify at the first bao-backed converge: each domain's AppRole login against
the Traefik ingress, and that `vault_kv2_get` returns the expected field names
once that domain's `secret/<path>` is seeded. `ai-public` additionally needs
its AppRole/policy created upstream (see above) and its role_id/secret_id
copied onto the `hermes_agent`/`open_webui` guests for their brain-sync
timers — the controller-side fetch and the guest-side runtime poll are two
separate consumers of the same identity.
