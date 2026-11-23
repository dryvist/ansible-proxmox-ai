# audiocpp

Runs the audio.cpp speech server (`audiocpp_server`, API-only, CPU backend) as
a systemd service. It serves OpenAI-style speech endpoints
(`POST /v1/audio/transcriptions`, `POST /v1/audio/speech`) and `GET /health`.

## Inputs

| Variable | Purpose |
| --- | --- |
| `audiocpp_models` | Served models: `id`, `family`, `file` (GGUF on the models mount), `task` (`asr` or `tts`). Required, non-empty. |
| `container_models_mount_path` | Read-only models mount that holds the GGUF files. Required. |
| `tofu_data.constants.service_ports.audiocpp_api` | Listen port, from the published inventory. Required. |

## Behaviour

- Installs the pinned upstream CPU release, verified by its published sha256.
- Asserts every model file exists on the mount before the service starts.
- Binds to `127.0.0.1` only. The server's model-management endpoints load
  server-local paths, so exposing the port is a separate decision.
- Starts with `--no-ui` and `lazy_load`. `/health` must answer before the role
  completes.

## Not in this role

- No play applies this role to any host yet.
- Model files are not placed on the mount by this role.
- No router route or alias is registered for these models.
