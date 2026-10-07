# Shared llama.cpp release contract

`defaults/main/00-release.yml` holds one Renovate-managed release tag and the
exact CPU, Vulkan, ROCm, CUDA, and CUDA runtime asset names used by the serving
roles. `tasks/download.yml` fetches an asset by its pinned URL through the
existing `APT_PROXY_URL` cache proxy. `tasks/prepare.yml` requires that proxy
before roles install packages or fetch external artifacts.

The three installers record the asset name after installation and compare it
on later converges, so the release archive is fetched again only when the
installed binary is absent, invalid, or out of date.
