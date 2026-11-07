# Pinned upstream response fixtures

`github-release-b11669.json` preserves the tag and the five declared assets'
names, IDs, and digest fields from the official GitHub release response.
Captured read-only on 2026-10-16 from
<https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11669>.
Other release fields and assets are omitted. Tests replay this response
through the existing checksum helper with a recording curl stand-in.
The stable release v0.7.0 maps to build b11669 through its official
`nightly-tag.txt` asset, preserved in `stable-release-v0.7.0-nightly-tag.txt`
from <https://github.com/ggml-org/llama.cpp/releases/download/v0.7.0/nightly-tag.txt>.

`github-release-b11960.json` preserves the same five assets from
<https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11960>,
captured on 2026-11-07. Stable v0.8.0 maps to b11960 through
`stable-release-v0.8.0-nightly-tag.txt`, captured from
<https://github.com/ggml-org/llama.cpp/releases/download/v0.8.0/nightly-tag.txt>.

`hf-2.4.0-update-check.txt` preserves the pinned CLI's complete native
`_check_cli_update` function, captured from
<https://raw.githubusercontent.com/huggingface/huggingface_hub/v2.4.0/src/huggingface_hub/cli/_cli_utils.py>.
The behavioral replay replaces network and installed-version lookups with
recording stand-ins and tests the supported environment flag.
Both role installation pins must match the replay fixture version.
