# Shared llama.cpp release contract

`defaults/main/00-release.yml` holds one Renovate-managed release tag and the
exact CPU, Vulkan, ROCm, CUDA, and CUDA runtime asset names and SHA-256 digests
used by the serving roles. `tasks/download.yml` fetches an asset by its pinned
URL through the published inventory's `cache_proxy_urls.apt_cache` list. The
inventory derives the list from tagged cache guests and its shared service-port
constant.
`tasks/prepare.yml` requires at least one endpoint before roles install
packages or fetch external artifacts.

The three installers record the asset name after installation and compare it
on later converges, so the release archive is fetched again only when the
installed binary is absent, invalid, or out of date.
