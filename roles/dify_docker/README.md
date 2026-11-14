# dify_docker

Deploys the Dify LLMOps platform as a Docker Compose stack in an LXC.

## Batch runtime inputs

| Input | Default | Runtime setting |
| --- | --- | --- |
| `dify_docker_workflow_max_execution_time` | `43200` seconds (12 hours) | `WORKFLOW_MAX_EXECUTION_TIME` |
| `dify_docker_plugin_max_execution_timeout` | `dify_docker_workflow_max_execution_time` | `PLUGIN_MAX_EXECUTION_TIMEOUT` and `PLUGIN_DAEMON_TIMEOUT` |
| `dify_docker_redis_appendonly` | `true` | Redis `--appendonly yes`, persisted on the Redis data mount |
| `dify_docker_celery_worker_amount` | `ai_agent_default_concurrency` | `CELERY_WORKER_AMOUNT`, with `CELERY_AUTO_SCALE=false` |
| `dify_docker_workflow_schedule_poller_enabled` | `true` | `ENABLE_WORKFLOW_SCHEDULE_POLLER_TASK` |
| `dify_docker_trigger_provider_refresh_enabled` | `true` | `ENABLE_TRIGGER_PROVIDER_REFRESH_TASK` |
| `dify_docker_trigger_url` | `dify_docker_public_url` | `TRIGGER_URL`, the public webhook base URL |
| `dify_docker_internal_trigger_url` | `http://nginx:80` | per-agent `<AGENT>_TRIGGER_URL` variables, the compose-internal webhook base |
| `dify_docker_otel_endpoint` | `ai_orchestration_otel_endpoint` | `OTLP_BASE_ENDPOINT`, the Cribl OpenTelemetry collector base URL |
| `dify_docker_agent_app_leaf_segment` | `agent-apps` | the OpenBao path segment of each catalog agent app's leaves, under `dify_docker_openbao_path` |

Celery concurrency applies per worker container across its subscribed queues.
Additional worker replicas multiply the active task capacity. `GRAPH_ENGINE_MAX_WORKERS`
remains the per-workflow engine limit, derived from `ai_agent_default_concurrency`.
The API, worker, and scheduler share the runtime environment, including enabled
OpenTelemetry export over HTTP/protobuf.
Gunicorn and Nginx read/send timeouts follow `dify_docker_workflow_max_execution_time`.
The plugin input applies in seconds; the daemon callback read budget converts it
to milliseconds for `DIFY_BACKWARDS_INVOCATION_READ_TIMEOUT`. The callback connection
idle budget uses seconds through `DIFY_INVOCATION_CONNECTION_IDLE_TIMEOUT`.

Dify 1.18 registers webhook routes without a separate enable flag. `TRIGGER_URL`
sets their advertised base URL; the schedule poller runs through `worker_beat`.
Nginx forwards `/triggers` to the API. The worker subscribes to Dify's native
workflow and trigger queues.

Before rendering Compose, the role enables AOF with `CONFIG SET appendonly yes`
on an existing Redis container and waits for a successful rewrite and write status.
Stopped containers start with their existing configuration for this conversion.
Subsequent converges retain AOF and verify persistence before any recreation.
The role requires an existing container when a `dump.rdb` snapshot is present.
Fresh installations use the Compose AOF setting directly. See the
[Redis persistence procedure](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).

Runtime names verified against Dify 1.18.0 on 2026-11-06:
[workflow and schedule settings](https://github.com/langgenius/dify/blob/1.18.0/api/configs/feature/__init__.py),
[trigger URL](https://github.com/langgenius/dify/blob/1.18.0/docker/.env.example), and
[worker concurrency and queues](https://github.com/langgenius/dify/blob/1.18.0/api/docker/entrypoint.sh).
Plugin transport settings follow the
[Dify API client](https://github.com/langgenius/dify/blob/1.18.0/api/core/plugin/impl/base.py)
and [daemon configuration](https://github.com/langgenius/dify-plugin-daemon/blob/0.6.12/internal/types/app/config.go).

## Installation

This role ships in the `ansible-proxmox-ai` repository and is applied via
`playbooks/site.yml`. No separate installation is required beyond cloning the
repo and installing collection dependencies:

```bash
git clone https://github.com/dryvist/ansible-proxmox-ai.git
cd ansible-proxmox-ai
ansible-galaxy collection install -r requirements.yml --force
```

## Prerequisites

- An LXC tagged `dify` (nesting enabled for Docker) with its data directory on
  a persistent mount.
- The secret store reachable, or the corresponding env fallbacks present.
- The role generates and stores Dify Agent runtime credentials through the
  shared OpenBao seed task on first converge.

## Usage

```bash
sops exec-env secrets.enc.yaml 'doppler run -- ansible-playbook playbooks/site.yml --tags dify_docker'
```

## SECRET_KEY

`SECRET_KEY` signs sessions and protects the material that decrypts what the
app has already stored. Two properties follow, and both are load-bearing:

- **It is part of the backup.** Data restored against a different key is
  unreadable. Nothing errors: the stack starts, the pages load, and the loss
  surfaces only when something tries to use a stored credential.
- **It must not live only beside the data it unlocks.** Its canonical home is
  the secret store; the generate-once file on the guest is the greenfield
  bootstrap and a local fallback, nothing more.

The role resolves it from the secret store first, then the guest, and **asserts
a non-empty result before deploying**. It never generates a replacement for an
install that already has one.

## Agent runtime credentials

Dify 1.18.0 runs an Agent backend and local shell sandbox. The role generates
and stores three independent secrets at `dify_docker_openbao_path`:
`DIFY_AGENT_API_TOKEN` authenticates the API/backend pair,
`DIFY_AGENT_SERVER_SECRET_KEY` protects Agent Stub tokens, and
`DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN` authenticates shellctl requests.
`DIFY_AGENT_SERVER_SECRET_KEY` is 43 characters of unpadded base64url that
decodes to exactly 32 bytes (its last character carries zero unused bits),
the format the Dify 1.18 Agent Stub enforces; a stored value that fails it
is replaced with a freshly generated one while every other stored field is
kept. Stored values remain authoritative except in that case; values supplied
through the environment seed only missing or rejected fields. The local
sandbox reaches the API only through the dedicated SSRF proxy's signed file
and Agent Stub routes.

## Agent DSL imports

The public agent list is read from `agents/dify/manifest.json` at the pinned
`dify_docker_agent_dsl_commit`. The manifest itself and every listed DSL are
verified against their pinned SHA-256 values before any public app is imported.
Catalog entries are imported as listed, including driver entries. The role
lists every Dify app page before matching catalog names. Each app description
records its catalog SHA-256 only after its workflow is published. A missing app
is created; by default, an existing app is overwritten in place only when its
recorded SHA-256 differs or is missing. An unchanged SHA-256 sends no import.
Dify keeps the existing app ID and mode during an overwrite. By default, console
edits to the app graph are overwritten only when the catalog file changes. Apps with
declared entries still write them on every converge; see
[declared catalog app variables](declared-app-variables.md). Successful
workflow imports are published so API callers receive the catalog version. API-key provisioning
also runs for unchanged apps, which lets a later converge repair an app whose
key was not stored. Import, confirmation, publication and catalog-hash update
failures fail the converge. Check mode previews public and private app creates
or overwrites, reads the pinned manifest and DSLs into memory, and writes no
files to the target or Dify APIs.

`dify_docker_agent_dsl_update_existing` (default `false`) re-imports every
selected public catalog app that already exists, even when its recorded
SHA-256 matches. The import targets the existing app ID, confirms pending
imports, republishes the workflow, and verifies that the ID is unchanged and
each published LLM node has `context`. New catalog apps still follow the normal
create path, apps outside the selected catalog are untouched, and private DSL
entries keep their existing reconciliation behavior.

`dify_docker_agent_dsl_select` (default `[]`) names the catalog entries one run
imports. Empty imports every manifest entry. A name missing from the manifest
fails the run before any catalog DSL fetch. A sliced run wires the rotation only
when its selection names the rotation app, so the last slice imports it.

`dify_docker_private_agent_dsls` (default `[]`) adds private sources alongside
the public catalog entries, each as `name`, `url` and `sha256`. The list and
`dify_docker_private_agent_dsl_token` (default empty) are supplied at converge time
from a private source and are never committed. Each private entry is fetched with
`Authorization: Bearer <token>`, verified against its `sha256`, and reconciled
like the public DSLs when its source is valid.
Private entries are skipped while the token is empty.

## Declared catalog app variables

Draft environment variables for catalog apps (`dify_docker_agent_app_variables`), their tool
provider placeholders (`dify_docker_agent_tool_providers`) and the per-app leaf option are
documented in [declared-app-variables.md](declared-app-variables.md). AppRole secret ids are minted
and read with the issuer AppRole named by `dify_docker_agent_issuer_env_names` and logged in through
`dify_docker_agent_issuer_auth_mount`, never with the converge writer token.

## Built-in tool providers

`dify_docker_agent_tool_provider_plugins` declares Marketplace plugins that the role installs before
catalog agent DSL imports. The default GitHub entry pins `langgenius/github` at `0.3.11`; the role
resolves the checksum-bearing identifier from Dify Marketplace and stops if the returned version
does not match the pin. It then uses the existing Dify console session to install or upgrade the
plugin and ensures the `langgenius/github/github` provider has a team-visible credential.

The GitHub token is read from `dify_docker_agent_tool_provider_plugins[].openbao_path` and
`openbao_field` (`GITHUB_READ_ONLY_TOKEN` by default). Store a fine-grained token limited to the
repositories needed by the agent and grant read access only, including Pull requests and Contents.
The Marketplace plugin also exposes write operations; GitHub's read-only permissions reject those
calls, while graph changes should add only list/read PR tools. The credential is created when its
declared name is absent. Dify's credential list does not expose the saved secret, so changing the
OpenBao token alone does not rotate a credential that Dify already stores.

In check mode, the role reads Marketplace, Dify and OpenBao state and reports missing plugin or
credential changes. It skips the console API writes.

## Tier2 rotation webhook wiring

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

## Agent MCP server publishing

`dify_docker_publish_agent_mcp` (default `false`) publishes every catalog
agent app as its native Dify MCP server after the DSL import, and stores each
server's URL at `dify_docker_agent_mcp_path`. The app id is resolved through
the same paged exact-name lookup as the API keys, the console MCP server is
created only when the app has none, and a server Dify reports as not `active`
is activated. The stored `url` is written through the shared `openbao_secrets`
seed task only when it is missing or differs, so an unchanged app costs no
write. A sliced run (`dify_docker_agent_dsl_select`) publishes only its
selected apps. Check mode makes no POST, PUT or storage write and reports
what a live run would change, by app name only.

## What in this stack needs protecting

| Component | Needs protection | Why |
| --- | --- | --- |
| Relational database | **Yes** | Apps, datasets, and stored provider credentials. Not reconstructible. |
| App storage directory | **Yes** | Uploaded source documents plus the key material the stored credentials depend on. The other half of a usable restore. |
| Vector store | **No** | A derived index, re-embedded from the uploads and their database records — both covered above. Far larger than what it duplicates. |
| Cache | **No** | Ephemeral by design. |

## Moving the relational database to the shared cluster

The database currently runs as a service inside this stack, on a mount the
guest-level backup mechanism cannot capture. The shared PostgreSQL cluster
already carries continuous WAL archiving, point-in-time recovery, and nightly
logical dumps, and moving there inherits all of it.

**One thing must be resolved before that move, and it is not cosmetic.** In this
stack the app's login role is the superuser of its own instance, which is what
lets its migrations create whatever extensions they need. On the shared cluster
a managed login role owns its database but is not a superuser, and creating an
untrusted extension needs one. The shared cluster's role already supports
declaring a database's extensions and enabling them as the superuser — the open
question is only **which** extensions this app's migrations require.

Resolve it by reading the extension list from a running instance
(`SELECT extname FROM pg_extension`) and declaring exactly those on the managed
database, rather than granting superuser to work around it. Until that list is
known, this role deliberately still runs its own database service: an app
pointed at a database whose migrations cannot complete is worse than one whose
database is merely in the wrong place.

Once the list is declared, the cutover follows the same recoverable ordering
used for the other app moved to this cluster: capture the key into the secret
store and verify it reads back; create the databases on the shared cluster while
the app is untouched; stop only the app and dump; load; repoint and converge;
verify a stored credential actually works before reclaiming anything.
