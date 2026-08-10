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

App creds are delivered from OpenBao `secret/ai/hermes` (`bao_local_llm_secrets`)
with an env fallback; the PEM is written to `{{ hermes_agent_hermes_home }}/github-app.pem`
(`0600`, `no_log`). The role stays inert until the creds are set.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_github_app_id` | `""` | GitHub App ID (bao/env) |
| `hermes_agent_github_app_installation_id` | `""` | App installation ID (bao/env) |
| `hermes_agent_github_app_private_key` | `""` | App PEM (bao/env; written to a 0600 file) |

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

## GitHub issues & projects

Delivers a fine-grained PAT (`GH_PAT_WRITE_PROJECT_ISSUES`) into `.env` giving
Hermes **read/write Issues across all repos** and **read/write Projects (v2) in
the `dryvist` org** — for triaging, creating and updating issues and managing
project boards. It is deliberately least-privilege: **not** for code commits (that
is the signed `docs-pr` / GitHub App path) and **not** for merges. Bao-first
(`secret/ai/hermes`, `bao_local_llm_secrets`) with an env fallback; empty until the
token is set. The bundled `dryvist/github-issues` skill documents the REST (issues)
and GraphQL (Projects v2) calls and the usage guardrails.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_github_issues_pat` | `""` | issues + org-projects PAT (bao/env) |
| `hermes_agent_github_read_token` | `""` | read-only org token for `github-maint` (bao/env) |

The `github-maint` profile gets `hermes_agent_github_read_token` under the same
`GH_PAT_WRITE_PROJECT_ISSUES` key instead — the key is what the skill reads, the
value is what carries the scope. Every other profile renders the key blank, so
the read/write PAT above never leaves the default profile.
