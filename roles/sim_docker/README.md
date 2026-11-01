# sim_docker

Deploys [Sim](https://github.com/simstudioai/sim), a visual agent-workflow builder, as an isolated
evaluation instance on a Docker-in-LXC guest. The stack is Sim's official production compose file,
unchanged, plus a local override. Docs: <https://docs.sim.ai/platform/self-hosting>

## Installation

This role ships in this repository. Install the collection dependencies once:

```bash
ansible-galaxy collection install -r requirements.yml --force
```

## Usage

The role runs from its own playbook, `playbooks/specialist-eval.yml`, on hosts in `sim_eval_group`
(inventory tag `sim-eval`). `site.yml` does not import that playbook:

```bash
ansible-playbook playbooks/specialist-eval.yml --tags sim_docker
```

The controller environment must provide the converge writer token used for the secret seed
(`OPENBAO_SEED_WRITER_TOKEN`). The play is isolated: a failure is recorded and the remaining plays
still run.

## What gets deployed

- Vendor file: `docker-compose.prod.yml` at release `v0.9.32`, commit `df3db64e`. The role fetches it
  from the commit URL and rejects it unless its SHA-256 matches `sim_docker_compose_sha256`.
- Local override: `docker-compose.override.yml`, rendered from `templates/docker-compose.override.yml.j2`.
  It pins digests for the three Sim images and for `db` and `redis`, bind-mounts the uploads
  directory at `/app/uploads`, and moves the Postgres data directory to a bind mount.
- `.env`: rendered from `templates/env.j2` with mode 0600. Compose reads it for interpolation, and the
  vendor file passes it to the app and realtime services. It carries `SIM_VERSION` (app, realtime,
  migrations), `SIM_CRON_VERSION` (cron, tag and digest) and the seeded secrets.
- Both compose files are passed explicitly with `-f`. The vendor filename is not the default name, so
  compose does not discover the override by itself.

## Pinned release

- `SIM_VERSION` is `v0.9.32`, published 2026-10-23T03:22:48Z. The digests are the registry manifest
  digests for each tag, resolved 2026-10-31. Changing the version means changing the digests in the
  defaults too. A digest takes precedence over a tag, so a `SIM_VERSION` edit alone does not move the
  running images.
- `pgvector/pgvector:pg17` and `redis:7-alpine` are pinned by digest in the override.

## Secrets

Seeded at `sim_docker_openbao_path` (`ai/eval/specialist-platform/sim`) through the shared
`seed_generated` task:

- `POSTGRES_PASSWORD`, `BETTER_AUTH_SECRET`, `ENCRYPTION_KEY`, `INTERNAL_API_SECRET`,
  `API_ENCRYPTION_KEY`, `CRON_SECRET`

Values are generated only while PostgreSQL has not initialized its data directory. After that, a
missing field fails the run. The writer-policy assertion is not set.

## Hosted features

- Chat: `NEXT_PUBLIC_CHAT_DISABLED=true` is written to `.env`. `COPILOT_API_KEY` and
  `SIM_AGENT_API_URL` are not written, so they stay empty as in the vendor file.
- Anonymous telemetry: `NEXT_TELEMETRY_DISABLED=1` is written to `.env`.

The vendor file leaves these off unless configured, and this role does not configure them: billing
(`BILLING_ENABLED`), PostHog, enterprise features (`ENTERPRISE_ENABLED`), remote Function sandboxes,
OTLP export, email providers (messages go to the console), Ollama (`OLLAMA_URL`), and hosted tool keys.

## Variables

- `sim_docker_public_url`: the origin browsers use. The default is `http://localhost:3000`.
- `sim_docker_socket_url`: realtime URL for browsers that do not reach `/socket.io` through a reverse
  proxy. Empty by default.
- `sim_docker_llm_base_url`: OpenAI-compatible endpoint for model blocks. Empty by default. When set,
  `.env` gets `VLLM_BASE_URL`, and `VLLM_API_KEY` is read from the controller environment variable
  named by `sim_docker_llm_api_key_env` (default `SIM_LLM_API_KEY`).

## Tests

```bash
python -m pytest tests/sim_docker -q
```

The tests check the vendor checksum and commit pin, the default secret path, the override (no
published database port, the uploads mount, digest pins), the `.env` contents, the seed, and the
isolation of the playbook from `site.yml`.

## Known gaps

- The converge writer policy must allow writes to `ai/eval/specialist-platform/sim`. This role does
  not change policy.
- Email is not configured, so account verification and invitation messages are logged, not sent.
- The vendor file publishes ports 3000 and 3002 on all host interfaces.
- The vendor file uses long-form `env_file` entries, so it needs Docker Compose 2.24.0 or later.
