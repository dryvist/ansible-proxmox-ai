# Dify agent DSL catalog fixtures

`agent-dsl-manifest-<commit>.json` preserves the exact bytes of
`agents/dify/manifest.json` at the ai-llm-prompts commit pinned in
`roles/dify_docker/vars/agent_dsls.yml`, captured read-only from
`https://raw.githubusercontent.com/dryvist/ai-llm-prompts/<commit>/agents/dify/manifest.json`.

`test_agent_dsl_manifest.py` hashes this fixture and requires the hash to equal
`dify_docker_agent_dsl_manifest_sha256`, so a commit bump that leaves the
checksum stale (or a hand-edited pin or fixture) fails CI instead of failing
the converge's first `get_url`. The fixture name carries the commit it
belongs to: a commit without a fixture is a bump this script has not synced
yet.

`tier2-rotation-dsl.yml` is a representative rendering of the rotation catalog
DSL (empty per-agent `<AGENT>_TRIGGER_URL`/`<AGENT>_WEBHOOK_ID` pairs plus
rotation knobs) used by `test_rotation_wiring.py` to render the roster the
wiring derives; its agent names are invented, since the real roster lives in
ai-llm-prompts and must never be hard-coded here.

One command refreshes the fixture and the checksum pin together, after a
Renovate commit bump:

```sh
python3 scripts/update_dify_manifest_pin.py
```

The script never changes the pinned commit itself; Renovate owns that.
