#!/usr/bin/env bash
# Stage one multi-GB GGUF: resumable download to <dest>.part, size check
# against the origin's Content-Length, then an atomic rename. A partial file
# never sits at <dest>, so a presence gate on <dest> is a correctness gate.
set -euo pipefail

url=$1
dest=$2
part="$dest.part"

curl -sSfL -C - --retry 10 --retry-all-errors --retry-delay 5 \
  -o "$part" "$url"

expected=$(curl -sSIL "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:"{n=$2} END{print n}')
actual=$(stat -c %s "$part")
if [ -z "$expected" ] || [ "$actual" != "$expected" ]; then
  echo "size mismatch for $part: have $actual, origin says ${expected:-unknown}" >&2
  exit 1
fi

mv -f "$part" "$dest"
