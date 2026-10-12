#!/usr/bin/env bash
# Reinstall the Galaxy dependencies from requirements.yml, retrying a failed run.
#
# galaxy.ansible.com resets connections when a wide job matrix installs at once, and
# ansible-galaxy aborts on the first reset. Each attempt re-reads requirements.yml and
# --force keeps the reinstall fresh, so a retry is the same command, not a weaker one.
set -euo pipefail

attempts=4
for attempt in $(seq 1 "$attempts"); do
  if ansible-galaxy install --force -r requirements.yml; then
    exit 0
  fi
  echo "::warning::ansible-galaxy install failed (attempt ${attempt}/${attempts})"
  if [ "$attempt" -lt "$attempts" ]; then
    sleep $((attempt * 20))
  fi
done
exit 1
