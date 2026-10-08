# Pinned upstream response fixtures

`github-release-b11459.json` preserves the tag and the five declared assets'
names, IDs, and digest fields from the official GitHub release response.
Captured read-only on 2026-10-07 from
<https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11459>.
Other release fields and assets are omitted. Tests replay this response
through the existing checksum helper with a recording curl stand-in.

`hf-2.1.1-update-check.txt` preserves the pinned CLI's complete native
`_check_cli_update` function, captured from
<https://raw.githubusercontent.com/huggingface/huggingface_hub/v2.1.1/src/huggingface_hub/cli/_cli_utils.py>.
The behavioral replay replaces network and installed-version lookups with
recording stand-ins and tests the supported environment flag.
Both role installation pins must match the replay fixture version.
