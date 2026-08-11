#!/usr/bin/env bash
# Assert every checksum-verified installer pin matches its pinned version.
#
# Renovate proposes version strings but cannot compute a checksum, so a bumped
# version arrives beside a stale sha256. The role treats that as a tamper guard
# and fails the converge — but by then the broken update has already merged.
# This runs on every pull request so the mismatch is caught while it is still a
# reviewable diff.
#
#   --fix   rewrite the sha to the computed value (used on Renovate branches)
#   (none)  verify only; non-zero exit on any mismatch
#
# Adding a pin: append a row to PINS. Fields are
#   <defaults file>|<version var>|<sha var>|<url template with %s for version>
set -o errexit
set -o nounset
set -o pipefail

FIX=0
[[ "${1:-}" == "--fix" ]] && FIX=1

readonly PINS=(
  "roles/hermes_agent/defaults/main/10-installer-and-bundles.yml|hermes_agent_version|hermes_agent_installer_sha256|https://raw.githubusercontent.com/NousResearch/hermes-agent/%s/scripts/install.sh"
)

# One workdir for the whole run, removed once. A trap set inside the loop would
# be overwritten each iteration, leaking every downloaded file but the last.
workdir=$(mktemp -d)
# shellcheck disable=SC2064 # expand workdir now, at trap-set time
trap "rm -rf '$workdir'" EXIT

fail=0

for pin in "${PINS[@]}"; do
  IFS='|' read -r file version_var sha_var url_tmpl <<<"$pin"

  if [[ ! -f "$file" ]]; then
    echo "FAIL ${file}: no such file — did the role's defaults layout change?" >&2
    fail=1
    continue
  fi

  # Deliberately not a YAML parse: these values are plain quoted scalars, and a
  # yaml dependency in a gate that must run everywhere is not worth it.
  version=$(sed -nE "s/^${version_var}: *\"?([^\"]+)\"?/\1/p" "$file" | head -1)
  pinned=$(sed -nE "s/^${sha_var}: *\"?([0-9a-f]{64})\"?/\1/p" "$file" | head -1)

  if [[ -z "$version" || -z "$pinned" ]]; then
    echo "FAIL ${file}: could not read ${version_var} and/or ${sha_var}" >&2
    fail=1
    continue
  fi

  # shellcheck disable=SC2059 # url_tmpl is a trusted format string from PINS
  url=$(printf "$url_tmpl" "$version")

  # Downloaded to a file, never through a shell variable: command substitution
  # strips trailing newlines, so `$(curl ...)` hashes different bytes than the
  # server sent and every check fails with a plausible-looking mismatch.
  tmp="${workdir}/$(basename "$file").installer"

  if ! curl -fsSL --max-time 30 --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
    echo "FAIL ${version_var}=${version}: cannot fetch ${url}" >&2
    echo "     A version whose installer does not exist is not a version to pin." >&2
    fail=1
    continue
  fi

  actual=$(shasum -a 256 "$tmp" | cut -d' ' -f1)

  if [[ "$actual" == "$pinned" ]]; then
    echo "OK   ${version_var}=${version} sha matches"
    continue
  fi

  if (( FIX )); then
    # Anchored to the exact 64-hex value read above, so nothing else moves.
    sed -i.bak "s/${pinned}/${actual}/" "$file" && rm -f "${file}.bak"
    echo "FIXED ${version_var}=${version} sha ${pinned:0:12}... -> ${actual:0:12}..."
  else
    echo "FAIL ${version_var}=${version} sha MISMATCH" >&2
    echo "     pinned:   ${pinned}" >&2
    echo "     computed: ${actual}" >&2
    echo "     The version moved and the checksum did not. Run:" >&2
    echo "       .github/scripts/check-installer-sha.sh --fix" >&2
    fail=1
  fi
done

exit "$fail"
