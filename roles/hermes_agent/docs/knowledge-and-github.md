# LLM knowledge base (llm-wiki)

Enables the bundled `research/llm-wiki` skill so Hermes builds and maintains an
interlinked Markdown "second brain" from raw sources (build / query / lint /
maintain, with SHA256 source-drift detection). The wiki lives at `WIKI_PATH` =
`{{ hermes_agent_wiki_path }}` (`/var/lib/hermes/wiki`) — under the persistent
ZFS volume, so it is snapshotted and replicated. A nightly cron seeds a
lint/health-check. Context compression is enabled (`summary_model` pointed at the
router, since the upstream Google default is unreachable here) so long autonomous
sessions don't overflow.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_wiki_enabled` | `true` | enable llm-wiki + create the wiki dir |
| `hermes_agent_wiki_path` | `{{ hermes_agent_home }}/wiki` | persistent wiki root (`WIKI_PATH`) |
| `hermes_agent_context_compression_enabled` | `true` | auto-shrink long sessions |
| `hermes_agent_context_compression_threshold` | `0.75` | compress at 75% of context |
| `hermes_agent_nightly_wiki_cron_*` | — | nightly lint/health-check cron |

## Autonomous GitHub docs-contributor

Gives Hermes a **read public dryvist repos + open signed, draft, no-merge doc PRs**
capability against `dryvist/docs` and `dryvist/docs-starlight`, via a dedicated
GitHub App (`hermes-docs-bot`). Commits are authored through the
`createCommitOnBranch` GraphQL mutation so GitHub marks them **Verified/signed**
(a plain `git push` is rejected by the org's required-signatures ruleset). The
bundled `dryvist/docs-pr` skill enforces the guardrails: draft-only, attribution
triad, dated branches, `docs:` Conventional-Commit titles, per-repo/day caps +
de-dup, secret redaction, and absolute privacy routing (sensitive → docs-starlight
only). **No-merge** is guaranteed by the org ruleset (human review + signatures,
the App is not a bypass actor), not by the token scope.

GitHub access uses installation tokens minted from the OpenBao GitHub mount
(`hermes_agent_github_identity_mount`), one AppRole per trust boundary
(`public`, `private`). `hermes-gh-token.timer` (root) writes one token file per
`<set>-<boundary>` under `hermes_agent_github_identity_token_dir` (0600, service
user). `/usr/local/bin/gh` (`files/hermes-gh.py`) picks the file from
`HERMES_GH_TOKEN_SET` (default `review`) and `HERMES_TRUST_BOUNDARY`, refuses a
target repository outside that boundary, and refuses a write to a public
repository whose outgoing text matches a private address, an internal domain
suffix, a private repository name, a secret path or a token shape. No PEM is
stored on the guest.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_github_identity_enabled` | `false` | Deploy helper, wrapper and timer |
| `hermes_agent_github_identity_approles` | env `HERMES_GITHUB_{PUBLIC,PRIVATE}_VAULT_{ROLE,SECRET}_ID` | Per-boundary AppRole credentials (root-only file) |
| `hermes_agent_github_identity_sets` | `review`/`author` x `public`/`private` | Permission set names |
| `hermes_agent_github_trust_boundary_default` | `private` | Exported to the gateway as `HERMES_TRUST_BOUNDARY` |
| `hermes_agent_github_guard_internal_suffixes` | env `PROXMOX_SUBDOMAIN` + `HERMES_GH_GUARD_INTERNAL_SUFFIXES` | Outbound gate domain list |
| `hermes_agent_github_guard_private_repos` | env `HERMES_GH_GUARD_PRIVATE_REPOS` | Outbound gate repository list |
| `hermes_agent_github_app_slug` | `jacobs-hermes-agent` | Exported as `HERMES_GITHUB_APP_SLUG` |

Helper unit tests live with the skill in
[nix-hermes](https://github.com/dryvist/nix-hermes)
(`data/skills/dryvist/docs-pr/tests/`) — run `python -m pytest` from that
skill dir (all guardrail logic, no network).

## Content bundle (nix-hermes)

The dryvist skills (docs-pr, github-issues, zammad-incidents, splunk-monitor)
and `SOUL.md` are CONTENT owned by the
[nix-hermes](https://github.com/dryvist/nix-hermes) flake, pinned here by
`hermes_agent_bundle_flake_ref` (a release tag). The converge builds that ref
on the **controller** (`nix build`, guarded by a Layer-1 assert) and
byte-copies the result into `$HERMES_HOME` — the guest never needs nix.
`SOUL.md` is composed at build time from `ai-assistant-instructions`'
`autonomous-base.md` plus the Hermes variant, so no vendored copy can drift.
Renovate bumps the pin on each nix-hermes release; edit skills/persona there,
never in this role.

## GitHub issues

Default-profile scheduled work uses the existing `hermes-gh` wrapper. The root
timer mints short-lived installation tokens from the guest's existing OpenBao
AppRoles and writes them to mode-0600 files under `/run/hermes-gh`. The wrapper
selects a token for each `gh api` call, checks the public/private repository
boundary, and applies the configured outgoing-content gate to public writes.
The role-local `hermes_agent/github-issues-api` skill uses that wrapper. This path
does not support organization Projects v2 mutations.

The `github-maint` profile remains read-only and receives
`hermes_agent_github_read_token` under the `GH_PAT_WRITE_PROJECT_ISSUES` key
because the bundled read-only skill expects that name. Its API scope prevents
issue and repository writes. Other profiles receive an empty value.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_github_read_token` | `""` | read-only org token for `github-maint` (bao/env) |
