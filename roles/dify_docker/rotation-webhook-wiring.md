# Tier2 rotation webhook wiring

After the agent DSL import, the role wires the `tier2-rotation` catalog
workflow (`dify_docker_rotation_app_name`) to the agent apps it fans out to.
The agent set is read from the rotation draft itself: every
`<AGENT>_TRIGGER_URL` variable that has a matching `<AGENT>_WEBHOOK_ID` names
one agent, and its Dify app name is the key lowercased with underscores folded
to hyphens (`AI_NEWS` maps to the app `ai-news`). No agent list is kept in the
role. Each agent app is resolved by exact name through the paged app lookup,
its webhook trigger is located through the console triggers API, and its bare
webhook id is read from the webhook endpoint for the trigger's node. A missing
agent app or webhook trigger fails the converge with the agent's app name.

Each `<AGENT>_TRIGGER_URL` is set to `dify_docker_internal_trigger_url`
without a trailing slash and each `<AGENT>_WEBHOOK_ID` to the bare webhook id
through one `environment_variable_patch` on the rotation draft, which is then
published; the rotation's own schedule trigger and each agent's webhook trigger are
enabled when Dify reports them disabled. The step is idempotent: when every
value already matches, no draft sync or publish is sent. Dify masks secret
values in the draft response, so a masked non-empty `<AGENT>_WEBHOOK_ID`
cannot prove the stored id is current. Each agent instead keeps a non-secret
companion string variable `<AGENT>_WEBHOOK_DIGEST` holding the SHA-256 digest
of the webhook id: the id and the digest are rewritten together whenever the
stored digest is missing or differs and left alone when it matches, with the
digest appended through the same patch under a deterministic id when the
variable is new and the existing variable's id reused when it is already
present by name. To force a reseed after a webhook is recreated, delete the
`<AGENT>_WEBHOOK_DIGEST` variable once in the console. The live operator
inputs `ROTATION_SPEED`, `ROTATION_OFFSET` and `ROTATION_PAUSED` are never
written. Check mode reads the same endpoints, reports the variable names that
would change, and writes nothing. All Dify 1.18.0 console endpoints above are
verified against that release's source.

The rotation's HTTP request node dispatches from inside the stack, through the
ssrf_proxy. The per-agent variables therefore carry
`dify_docker_internal_trigger_url` — the stack's own `nginx` service on the
Compose network over plain HTTP, which routes `/triggers` to the API — rather
than the public `dify_docker_trigger_url`, whose name resolves to a private
address the proxy refuses. The ssrf_proxy squid config allows exactly that
origin (derived from the variable, so an override moves the rule with it) on
that origin's own port, for one webhook id path — `/triggers/webhook/<id>`
where the id is a single run of letters, digits, underscores or hyphens, end
anchored — placed before the deny-private rule. The end anchor matters: nginx
resolves dot segments before location matching, so a bare path prefix would
let a proxied traversal land on another location. Dot segments,
percent-encoded paths, extra segments, other ports, and every other path on
the internal origin remain denied. `TRIGGER_URL` itself stays the public base
for callers outside the stack.
