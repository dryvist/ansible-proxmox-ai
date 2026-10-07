# Pinned upstream response fixtures

`github-release-b11459.json` preserves the tag and the five declared assets'
names, IDs, and digest fields from the official GitHub release response.
Captured read-only on 2026-10-07 from
<https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11459>.
Other release fields and assets are omitted. Tests replay this response
through the existing checksum helper with a recording curl stand-in.
