# LiteLLM Admin UI SSO

Split out of `roles/llm_router/README.md` to keep both files under the
repository's per-file token budget (`.token-limits.yaml`).

`/ui` signs in only via Authelia (LiteLLM generic OIDC; env contract
`defaults/main/65-oidc.yml`, redirect `<PROXY_BASE_URL>/sso/callback`).
`PROXY_ADMIN_ID` is the operator email (APPS authelia `authelia_admin_email`).
With the client secret resolved (bao `secret/apps/authelia`, env fallback
`LITELLM_OIDC_CLIENT_SECRET`), the env block renders and `general_settings`
sets `disable_env_credential_login` and
`disable_password_login_when_sso_enabled`; without it, none of these render.
API bearer auth is unaffected; on a UI lockout the master key still works over
the API. Boards link the router at `/ui` via the ingress `url_path`.
