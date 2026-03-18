#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

for f in "$DIR"/*.md; do
  echo "=== $f ==="
  cat "$f"
  echo
  echo "(paste the above into Codex, then press enter to continue)"
  read -r _
  echo

done
