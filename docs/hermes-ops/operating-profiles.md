# Operating profiles

See `hermes_agent_profiles` in `defaults/main.yml` and "Operating profiles" in
the [role README](../../roles/hermes_agent/README.md) for the concept, the
decision rule, and the current `splunk-admin` / `homelab-admin` profiles.
This section is the manual verification runbook.

## Profile smoke test (run once, after adding or changing a profile)

Not part of the converge — it burns a real LLM run, so it stays manual.
Verifies the three things the profiles design assumed and did not have a
live check for before this: the dispatcher actually spawns into the named
profile, a named-profile worker can post to Slack using the shared bot token
from **its own** `.env`, and its scoped MCP server(s) resolve.

```bash
sops exec-env secrets.enc.yaml 'doppler run -- \
  ansible-playbook -i inventory/hosts.yml playbooks/site.yml \
  --tags hermes_agent'

# On the guest, as the hermes user:
hermes profile list   # both profiles present
hermes kanban create 'profile smoke' --assignee splunk-admin \
  --body 'Post one line "splunk-admin profile smoke OK" to #hermes-all, then kanban_complete.' \
  --idempotency-key "profile-smoke-$(date -u +%Y-%m-%d)"
```

Confirm in order: (1) `kanban runs` shows the card dispatched with
`assignee=splunk-admin`, not `skipped_nonspawnable`; (2) the Slack post
arrives in #hermes-all; (3) the run's log shows the Splunk MCP resolving
(no "MCP server unavailable" for `splunk`). Repeat with `--assignee
homelab-admin` and a Zammad-shaped ask to cover the second profile.

## Memory-scope check (optional, only if the pinned hermes-agent version changes)

The "shared across profiles, by design" claim above was verified by reading
the pinned hermes-agent's hindsight plugin source, not by a live probe. If
`hermes_agent_version` ever moves, re-verify with:

```bash
# As the hermes user, in each profile:
hermes -p splunk-admin memory add "canary fact: splunk-admin wrote this"
hermes -p default memory recall "canary fact"   # should find it if global
```

If it does NOT find it, the plugin's default scoping changed upstream —
update the "shared across profiles" claim above and reconsider whether
`daily-summary` still needs to stay on the default profile.

## Add / remove a profile

Add: append an entry to `hermes_agent_profiles` (`mcp`, `env`, `skills`,
`soul_addendum_file`), add the matching `templates/soul-<name>.md.j2`
addendum, converge, then run the smoke test above before routing any real
card to it.

Remove: `hermes profile delete <name> -y` on the guest, then remove the
entry from `hermes_agent_profiles` and re-point every card that named it
back to `assignee: ""` (or another profile) in the same change — `assert.yml`
will fail the converge if a card still names a profile that no longer
exists. Only that profile's accumulated state (memories, wiki notes, session
history) is lost; the shared board, the default profile, and every other
profile are untouched.

## Concurrency: raising `max_in_progress`

`hermes_agent_kanban_max_in_progress` is pinned to `1` — the SUM cap across
every profile combined, not per profile (`max_in_progress_per_profile`
handles that split). This is today's measured-safe ceiling with a single
serving stream; naming more profiles does not raise it by itself. Raising it
back to 2+ (permitting real cross-profile overlap, at the measured ~0.71x
aggregate throughput) is a deliberate operator decision to make ONLY after
the serving tier is proven to have that capacity — never a side effect of
adding a profile.
