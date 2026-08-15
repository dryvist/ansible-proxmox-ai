#!/usr/bin/env bash
# Emit the molecule scenarios on disk as a JSON array, for a derived CI matrix.
#
# The matrix used to be a hand-written list in the workflow. A hand list fails
# by OMISSION: adding molecule/<name>/ and forgetting to extend the list leaves
# a scenario that never runs, and the workflow is green either way — the same
# silent-omission shape as an add-if-missing Ansible task. Nothing reports it,
# because a check that never executes and a check that passed look identical in
# a run summary. A derived matrix cannot omit what exists.
#
# Writes `scenarios=<json>` to $GITHUB_OUTPUT when running under Actions, and
# always prints the discovered names so the job log carries the evidence.
#
# Exits non-zero when zero scenarios are found. "Found nothing" must fail
# rather than produce an empty matrix, which would skip every test and still
# report success.
set -o errexit
set -o nounset
set -o pipefail

readonly MOLECULE_DIR="${1:-molecule}"

if [[ ! -d "$MOLECULE_DIR" ]]; then
  echo "No such directory: $MOLECULE_DIR" >&2
  exit 1
fi

# -mindepth/-maxdepth 2 keeps this to molecule/<scenario>/molecule.yml and does
# not descend into a scenario's own fixtures.
names="$(
  find "$MOLECULE_DIR" -mindepth 2 -maxdepth 2 -name molecule.yml -printf '%h\n' |
    sed 's#.*/##' |
    sort -u
)"

if [[ -z "$names" ]]; then
  echo "No molecule scenarios found under $MOLECULE_DIR/." >&2
  exit 1
fi

# Scenario names become shell words in `molecule test -s`, and JSON values in
# the matrix. Constrain them rather than trusting whatever a directory is
# called.
if bad="$(printf '%s\n' "$names" | grep -vE '^[a-z0-9_]+$')"; then
  echo 'Scenario names must match ^[a-z0-9_]+$; rejected:' >&2
  printf '  %s\n' "$bad" >&2
  exit 1
fi

echo "Discovered $(printf '%s\n' "$names" | wc -l | tr -d ' ') molecule scenarios:"
printf '%s\n' "$names" | sed 's/^/  /'

json="$(printf '%s\n' "$names" | jq -R . | jq -cs .)"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "scenarios=$json" >>"$GITHUB_OUTPUT"
else
  echo "$json"
fi
