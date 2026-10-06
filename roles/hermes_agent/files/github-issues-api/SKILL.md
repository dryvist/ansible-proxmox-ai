---
name: hermes-agent-github-issues-api
description: Read and update GitHub issues through the Hermes GitHub CLI
version: 1.0.0
author: dryvist homelab
license: MIT
platforms: [linux]
metadata:
  hermes:
    category: research
    tags: [github, issues, dryvist]
---

# GitHub issues API

Use the `gh api` command for GitHub issue operations. The `gh` on `PATH`
enforces the selected public/private repository boundary. Do not call GitHub
with `curl` or pass an authorization header yourself.

Set `HERMES_TRUST_BOUNDARY=public` or `private` on each command according to
the target repository. Use the repository path in every request so the wrapper
can check the target.

Reads use the wrapper's `review` token by default. Set
`HERMES_GH_TOKEN_SET=author` on every issue mutation so the wrapper uses the
existing short-lived write token.

Read an issue:

```bash
HERMES_TRUST_BOUNDARY=private gh api "repos/$OWNER/$REPO/issues/$NUMBER"
```

List open issues:

```bash
HERMES_TRUST_BOUNDARY=public gh api "repos/$OWNER/$REPO/issues?state=open&per_page=50"
```

Create an issue:

```bash
HERMES_TRUST_BOUNDARY=public HERMES_GH_TOKEN_SET=author gh api "repos/$OWNER/$REPO/issues" \
  --method POST --input - <<'JSON'
{"title":"A concise title","body":"Evidence and next steps."}
JSON
```

Comment on an issue:

```bash
HERMES_TRUST_BOUNDARY=private HERMES_GH_TOKEN_SET=author gh api "repos/$OWNER/$REPO/issues/$NUMBER/comments" \
  --method POST --input - <<'JSON'
{"body":"A concise update."}
JSON
```

Update issue fields:

```bash
HERMES_TRUST_BOUNDARY=public HERMES_GH_TOKEN_SET=author gh api "repos/$OWNER/$REPO/issues/$NUMBER" \
  --method PATCH --input - <<'JSON'
{"labels":["triage"]}
JSON
```

Organization Projects v2 mutations are not supported by this issue workflow.
Skip them and state the required action.
