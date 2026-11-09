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

One command refreshes the fixture and the checksum pin together, after a
Renovate commit bump:

```sh
python3 scripts/update_dify_manifest_pin.py
```

The script never changes the pinned commit itself; Renovate owns that.
