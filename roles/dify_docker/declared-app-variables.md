# Declared catalog app variables

`dify_docker_agent_app_variables` sets draft environment variables on a catalog app. It is keyed
by catalog app name, and each entry names one variable and exactly one source:

| Source | Value written |
| --- | --- |
| `value` | Plain text from the caller. |
| `kv: {path, field}` | A field of a KV leaf on the converge KV mount. The value is never copied anywhere else. |
| `approle_role_id: {approle}` | The role id of the AppRole. |
| `approle_secret_id: {approle}` | A new secret id, minted with `dify_docker_agent_secret_id_ttl` (default `720h`) and tagged with the app name. |

`secret: true` stores the variable with the secret value type. An entry with no source, or with
two, fails the app before its import. The failure names the app and the variable, never a value.

AppRole role ids and secret ids are read and minted with an issuer AppRole, not the converge writer
token. The issuer's role id and secret id come from the environment variables named in
`dify_docker_agent_issuer_env_names`, and it logs in through the `dify_docker_agent_issuer_auth_mount`
auth mount, which the AppRole API calls use too. An app with an AppRole-sourced entry logs in
before its first AppRole read, in check mode too. The login token is a batch token. It is not revoked, and it is cleared when the app's iteration
ends. A missing credential or a refused login fails the app before its import. The failure names the
app and the environment variables, never a response or a value.

`dify_docker_agent_tool_providers` maps a placeholder token in the catalog DSL to a workspace tool
provider name. The import replaces each token with that provider's id, read from
`GET /workspaces/current/tool-providers`, so a provider id reaches the app graph only through an
import. A name must match exactly one provider, or the app fails before its import.

An app with declared entries, an MCP tool node, or an environment variable with an empty value in its
definition runs these checks before its import. They run in check mode too. A failure names the app
and the items only, never a value.

- A declared source must resolve. A value that is still a template token (`^<[A-Z_]+>$`) counts as
  unresolved.
- Every tool provider key must occur in the definition, and none may remain after the replacement.
- An MCP tool node (`provider_type: mcp`) whose `provider_id`, after the replacements, is not one of
  the workspace's tool provider ids fails the app. Built-in providers are not checked, because the
  definition references them by name. The failure names the node's tool name only. No provider id is
  shown.
- Every environment variable of the definition with an empty value needs a declared entry. The
  failure lists the undeclared names. A non-empty variable may stay undeclared.
- A declared variable whose name ends with `_MODE` must resolve to `shadow`, after trimming and
  lowercasing, unless the app is listed in `dify_docker_agent_live_apps`. Any other value fails the
  app.

An app with declared entries runs these steps on every converge:

1. Resolve every source. This only reads. Check mode stops here.
2. Import the catalog DSL when its hash changed, with the tokens replaced.
3. Write the declared variables to the draft. The write runs on every converge because minted
   secret ids expire, so a console edit to a declared variable is overwritten on the next converge.
4. Publish. A changed app is published after its write, so the published workflow carries the
   variables. An unchanged app gets a publish of its declared variables.
5. Prune. Each secret id is minted with the app's name in its metadata, under
   `dify_docker_agent_secret_id_app_metadata_key`. The prune considers only the ids that name the app
   being reconciled. It keeps the ids minted in this run, and of the app's other ids on that AppRole
   only the newest, and destroys the rest. Ids of other apps are never destroyed. Ids without the
   metadata key are never destroyed either, and the run reports how many there are.

An app whose only declaration is a tool provider map has no entries, so an unchanged converge runs
no write, publish or prune for it. Its provider ids reach the graph only through an import. Check
mode never mints, writes, publishes or prunes. It reports variable names only. Apps without
declared variables run the same tasks as before.

Placeholders only:

```yaml
dify_docker_agent_app_variables:
  example-app:
    - name: EXAMPLE_TOKEN
      secret: true
      kv: {path: example/app, field: token}
    - name: EXAMPLE_ROLE_ID
      approle_role_id: {approle: agent-example}
dify_docker_agent_tool_providers:
  example-app:
    __EXAMPLE_TOOL__: example-tool
```
