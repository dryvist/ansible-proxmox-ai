#!/usr/bin/env bash
# Ansible runner — prefers a short-lived SSH certificate from the OpenBao CA
# (ssh-certificate-authority ADR) over the shared static key, then runs the
# playbook. Invoke under your secrets manager so BAO_ADDR + the
# ansible-converge AppRole are ambient:
#   doppler run -- scripts/run-ansible.sh playbooks/site.yml [args...]
# Without those env vars the static PROXMOX_SSH_KEY_PATH flow is unchanged.
set -euo pipefail

usage() {
  echo "Usage: $0 <playbook> [ansible-playbook args...]"
  echo "Example: doppler run -- $0 playbooks/site.yml --limit vms"
  exit 1
}

[[ $# -lt 1 ]] && usage

PLAYBOOK="$1"
shift

# A converge from a checkout behind its remote branch deploys stale content
# and still exits 0 with a green play recap — nothing in the output
# distinguishes it from a real deployment. Refuse by default; ALLOW_STALE_CHECKOUT=1
# is the deliberate escape hatch for a pinned replay.
REPO_ROOT=$(git rev-parse --show-toplevel)
BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
git -C "$REPO_ROOT" fetch --quiet origin "$BRANCH"
LOCAL_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD)
REMOTE_SHA=$(git -C "$REPO_ROOT" rev-parse "origin/$BRANCH")
if [[ $LOCAL_SHA != "$REMOTE_SHA" ]] && [[ -z ${ALLOW_STALE_CHECKOUT:-} ]]; then
  BEHIND=$(git -C "$REPO_ROOT" rev-list --count "$LOCAL_SHA..$REMOTE_SHA")
  echo "ERROR: checkout is $BEHIND commit(s) behind origin/$BRANCH — refusing to converge." >&2
  echo "  local:  $LOCAL_SHA" >&2
  echo "  remote: $REMOTE_SHA" >&2
  echo "Run 'git pull --ff-only origin $BRANCH', or set ALLOW_STALE_CHECKOUT=1 for a deliberate pinned replay." >&2
  exit 1
fi

CERT_DIR=""
RUNNER_BAO_TOKEN=""

revoke_runner_token() {
  [[ -z $RUNNER_BAO_TOKEN ]] && return 0
  { set +x; } 2>/dev/null
  if curl -fsSL --max-time 10 -X POST \
    -H @<(printf 'X-Vault-Token: %s\n' "$RUNNER_BAO_TOKEN") \
    "$BAO_ADDR/v1/auth/token/revoke-self" >/dev/null 2>&1; then
    RUNNER_BAO_TOKEN=""
    return 0
  fi
  return 1
}

# shellcheck disable=SC2329 # false positive: invoked via `trap cleanup EXIT` below.
# Reproduced in isolation — shellcheck stops crediting the trap reference once
# the script ends in an explicit `exit`, which the run log below now does.
cleanup() {
  local status=$?
  revoke_runner_token || true
  [[ -n $CERT_DIR ]] && rm -rf "$CERT_DIR"
  return "$status"
}
trap cleanup EXIT

# Mint an ephemeral ed25519 keypair signed by ssh-client-ca/sign/
# automation-ansible (principal `ansible`, TTL <=1h). OpenSSH pairs
# id + id-cert.pub automatically via PROXMOX_SSH_KEY_PATH. No secret
# material on any command line.
mint_ssh_cert() {
  local mount=${SSH_CA_MOUNT:-ssh-client-ca} login token signed
  CERT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ansible-sshcert.XXXXXX") || return 1
  chmod 700 "$CERT_DIR"
  (umask 077 && ssh-keygen -q -t ed25519 -N '' -C "ansible-converge" -f "$CERT_DIR/id") || return 1
  { set +x; } 2>/dev/null
  login=$(jq -nc --arg r "$OPENBAO_APPROLE_ANSIBLE_ROLE_ID" --arg s "$OPENBAO_APPROLE_ANSIBLE_SECRET_ID" \
    '{role_id: $r, secret_id: $s}' \
    | curl -fsSL --max-time 10 -H 'Content-Type: application/json' --data @- \
      "$BAO_ADDR/v1/auth/approle/login") || return 1
  token=$(printf '%s' "$login" | jq -er '.auth.client_token') || return 1
  RUNNER_BAO_TOKEN=$token
  signed=$(jq -nc --rawfile pub "$CERT_DIR/id.pub" --arg ttl "${SSH_CERT_TTL:-1h}" \
    '{public_key: $pub, ttl: $ttl}' \
    | curl -fsSL --max-time 10 \
      -H @<(printf 'X-Vault-Token: %s\n' "$RUNNER_BAO_TOKEN") --data @- \
      "$BAO_ADDR/v1/$mount/sign/automation-ansible" \
    | jq -er '.data.signed_key') || return 1
  printf '%s\n' "$signed" > "$CERT_DIR/id-cert.pub"
  export PROXMOX_SSH_KEY_PATH="$CERT_DIR/id"

  if [[ -z ${BAO_TOKEN:-} ]]; then
    # The inventory resolver and controller-side OpenBao reads share this
    # short-lived token. Cleanup revokes it after ansible-playbook exits.
    export BAO_TOKEN=$RUNNER_BAO_TOKEN
  else
    # A caller-supplied token may carry broader human policy. Preserve it and
    # revoke the runner-owned signing token as soon as the cert is minted.
    revoke_runner_token || true
  fi
}

if [[ -n ${BAO_ADDR:-} && -n ${OPENBAO_APPROLE_ANSIBLE_ROLE_ID:-} && -n ${OPENBAO_APPROLE_ANSIBLE_SECRET_ID:-} ]]; then
  # FAIL-LOUD: when the cert env is present, a mint failure is an error — never
  # silently ride the static key (that masked a dead cert path once already).
  # Break-glass = run WITHOUT the BAO env, with PROXMOX_SSH_KEY_PATH set.
  if ! mint_ssh_cert; then
    echo "ERROR: OpenBao SSH cert mint FAILED and the cert env is present — refusing" >&2
    echo "the silent static-key fallback. Fix the cert path, or unset the OPENBAO_APPROLE_ANSIBLE_*" >&2
    echo "env and set PROXMOX_SSH_KEY_PATH to deliberately use the static break-glass key." >&2
    exit 1
  fi
  echo "Using a short-lived SSH certificate from the OpenBao CA (automation-ansible)."
elif [[ -z ${PROXMOX_SSH_KEY_PATH:-} ]]; then
  echo "ERROR: no SSH auth available — set BAO_ADDR + OPENBAO_APPROLE_ANSIBLE_* for cert" >&2
  echo "minting, or PROXMOX_SSH_KEY_PATH for the static break-glass key." >&2
  exit 1
fi

# Pin host identities: materialize the reviewed known_hosts (Doppler
# SSH_KNOWN_HOSTS, harvested over authenticated channels) and verify strictly.
# A rebuilt guest gets a new host key and fails closed until re-harvested —
# that is the intended tradeoff. These pinned options are PREPENDED and
# OpenSSH uses the first value per option, so appended caller extras cannot
# weaken them; GlobalKnownHostsFile is disabled so only the pin is consulted.
# Without the pin ambient, non-interactive runs fail closed for any host not
# already in the user's own known_hosts.
if [[ -n ${SSH_KNOWN_HOSTS:-} ]]; then
  if [[ -z $CERT_DIR ]]; then
    CERT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ansible-sshkh.XXXXXX")
    chmod 700 "$CERT_DIR"
  fi
  printf '%s\n' "$SSH_KNOWN_HOSTS" > "$CERT_DIR/known_hosts"
  chmod 600 "$CERT_DIR/known_hosts"
  export ANSIBLE_SSH_COMMON_ARGS="-o UserKnownHostsFile=$CERT_DIR/known_hosts -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes${ANSIBLE_SSH_COMMON_ARGS:+ $ANSIBLE_SSH_COMMON_ARGS}"
fi

# A converge is the highest-consequence thing this repo does; without a
# persisted log, reconstructing what happened after the fact means trusting
# a green recap or digging through unrelated evidence (sshd logins, reflog).
LOG_DIR="$REPO_ROOT/.ansible-run-logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$(basename "$PLAYBOOK" .yml).log"
echo "Logging this run to $LOG_FILE"
set +e
ansible-playbook "$PLAYBOOK" "$@" 2>&1 | tee "$LOG_FILE"
STATUS=${PIPESTATUS[0]}
set -e

# A --limit naming a group that matched nothing (bad group name, a group only
# populated by a DIFFERENT repo's inventory loader, a typo) still lets the
# play recap come back green — localhost (the inventory-loader host) always
# ran, so a naive "did anything run" check is never satisfied by absence.
# If --limit asked for anything beyond bare localhost, the recap must show at
# least one non-localhost host, or this run touched nothing it was asked to.
LIMIT_VAL=""
prev=""
for a in "$@"; do
  [[ $prev == "--limit" || $prev == "-l" ]] && LIMIT_VAL="$a"
  [[ $a == --limit=* ]] && LIMIT_VAL="${a#--limit=}"
  prev="$a"
done
NON_LOCALHOST_LIMIT=$(tr ',' '\n' <<<"$LIMIT_VAL" | grep -vx 'localhost' | grep -v '^$' || true)
if [[ -n $NON_LOCALHOST_LIMIT ]]; then
  RECAP_HOSTS=$(awk '/^PLAY RECAP/{f=1;next} f && NF{print $1}' "$LOG_FILE")
  NON_LOCALHOST_RECAP=$(grep -vx 'localhost' <<<"$RECAP_HOSTS" || true)
  if [[ -z $NON_LOCALHOST_RECAP ]]; then
    echo "ERROR: --limit ($LIMIT_VAL) asked for hosts beyond localhost, but the play recap shows only localhost — this run did nothing." >&2
    echo "Check the group name against the inventory loader that actually populates it (it may live in a different repo)." >&2
    STATUS=1
  fi
fi

echo "Run log: $LOG_FILE"
exit "$STATUS"
