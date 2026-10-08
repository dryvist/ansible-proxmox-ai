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

# The URL template's %s is filled with the VERSION as stored, so a pin whose
# upstream ref carries a prefix puts that prefix in the template — the variable
# itself stays bare so a version bump can write it cleanly.
readonly PINS=(
  "roles/hermes_agent/defaults/main/10-installer-and-bundles.yml|hermes_agent_version|hermes_agent_installer_sha256|https://api.github.com/repos/NousResearch/hermes-agent/contents/scripts/install.sh?ref=v%s"
)

# One workdir for the whole run, removed once. A trap set inside the loop would
# be overwritten each iteration, leaking every downloaded file but the last.
workdir=$(mktemp -d)
# shellcheck disable=SC2064 # expand workdir now, at trap-set time
trap "rm -rf '$workdir'" EXIT

fail=0

# Authenticated requests get their own rate-limit quota; anonymous fetches from
# shared runners are throttled with 429. Local runs without a token stay anonymous.
auth=()
[[ -n "${GITHUB_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}")

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
  accept=()
  [[ "$url" == https://api.github.com/* ]] && accept=(-H 'Accept: application/vnd.github.raw')

  if ! http_code=$(curl -fsS ${auth[@]+"${auth[@]}"} ${accept[@]+"${accept[@]}"} --max-time 30 --retry 3 --retry-delay 2 -w '%{http_code}' -o "$tmp" "$url") || [[ "$http_code" != 200 ]]; then
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

# Release asset digests come from one pinned GitHub release response. Keep this
# in CI: role converges consume these pins and never query the release API.
readonly RELEASE_PINS=(
  "roles/llamacpp_release/defaults/main/00-release.yml|llamacpp_release_tag|https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/%s"
)

for release_pin in "${RELEASE_PINS[@]}"; do
  IFS='|' read -r release_file release_var release_url_tmpl <<<"$release_pin"
  release_tag=$(sed -nE "s/^${release_var}: *\"([^\"]+)\"/\1/p" "$release_file" | head -1)
  if [[ ! "$release_tag" =~ ^b[0-9]+$ ]]; then
    echo "FAIL ${release_file}: no valid pinned release tag" >&2
    fail=1
    continue
  fi
  # shellcheck disable=SC2059 # release_url_tmpl is a trusted PINS format string
  release_url=$(printf "$release_url_tmpl" "$release_tag")
  release_metadata="${workdir}/release.json"
  # API responses are read directly: never forward auth headers on redirects.
  if ! http_code=$(curl -fsS ${auth[@]+"${auth[@]}"} --max-time 30 --retry 3 --retry-delay 2 -w '%{http_code}' -o "$release_metadata" "$release_url") || [[ "$http_code" != 200 ]]; then
    echo "FAIL ${release_var}=${release_tag}: cannot fetch release metadata" >&2
    fail=1
    continue
  fi
  if ! jq -e --arg tag "$release_tag" '.tag_name == $tag and (.assets | type == "array")' "$release_metadata" >/dev/null; then
    echo "FAIL ${release_var}=${release_tag}: invalid release metadata" >&2
    fail=1
    continue
  fi

  release_assets=$(sed -nE 's/^(llamacpp_release_[a-z_]+_asset): *"([^"]+)"/\1|\2/p' "$release_file")
  if [[ -z "$release_assets" ]]; then
    echo "FAIL ${release_file}: no declared release assets" >&2
    fail=1
    continue
  fi
  while IFS='|' read -r asset_var asset_template; do
    asset_placeholder="{{ ${release_var} }}"
    asset_name="${asset_template//"$asset_placeholder"/$release_tag}"
    sha_var="${asset_var%_asset}_sha256"
    pinned=$(sed -nE "s/^${sha_var}: *\"([0-9a-f]{64})\"/\1/p" "$release_file" | head -1)
    digest=$(jq -r --arg name "$asset_name" '[.assets[] | select(.name == $name) | .digest] | if length == 1 then .[0] else empty end' "$release_metadata")
    if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ || -z "$pinned" ]]; then
      echo "FAIL ${asset_name}: requires one official SHA-256 digest and one pin" >&2
      fail=1
      continue
    fi
    actual="${digest#sha256:}"
    if [[ "$actual" == "$pinned" ]]; then
      echo "OK   ${asset_name} sha matches"
    elif (( FIX )); then
      sed -i.bak "s/^${sha_var}: *\"${pinned}\"/${sha_var}: \"${actual}\"/" "$release_file" && rm -f "${release_file}.bak"
      echo "FIXED ${asset_name} sha ${pinned:0:12}... -> ${actual:0:12}..."
    else
      echo "FAIL ${asset_name} sha MISMATCH" >&2
      fail=1
    fi
  done <<<"$release_assets"
done

exit "$fail"
