#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
export RUN_ANSIBLE_IDENTITIES="${RUN_ANSIBLE_IDENTITIES:-signer,semaphore?,ansible}"
export RUN_ANSIBLE_FALLBACK_ON_ERROR="${RUN_ANSIBLE_FALLBACK_ON_ERROR:-1}"
export RUN_ANSIBLE_EXPORT_CONVERGE_IDENTITY="${RUN_ANSIBLE_EXPORT_CONVERGE_IDENTITY:-1}"
export RUN_ANSIBLE_CHECKOUT_GUARD="${RUN_ANSIBLE_CHECKOUT_GUARD:-1}"
export RUN_ANSIBLE_LIMIT_LOCALHOST="${RUN_ANSIBLE_LIMIT_LOCALHOST:-1}"
export RUN_ANSIBLE_RECAP_CHECK="${RUN_ANSIBLE_RECAP_CHECK:-1}"
export RUN_ANSIBLE_LOG_DIR="${RUN_ANSIBLE_LOG_DIR:-$REPO_ROOT/.ansible-run-logs}"
export RUN_ANSIBLE_RETIRING_IDENTITIES="${RUN_ANSIBLE_RETIRING_IDENTITIES:-ansible}"

# renovate: datasource=github-releases depName=dryvist/homelab-contracts
HOMELAB_CONTRACTS_REF=v5.34.0
exec nix run "github:dryvist/homelab-contracts/${HOMELAB_CONTRACTS_REF}#run-ansible" -- "$@"
