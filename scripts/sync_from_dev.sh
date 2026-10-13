#!/usr/bin/env bash
# Pull the latest engine from the development repo (qs-ai-hybird) into this deployable repo.
set -euo pipefail
DEV="${1:-../qs-ai-hybird}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
for d in qs_hybrid profiles tests; do rm -rf "$HERE/$d"; cp -r "$DEV/$d" "$HERE/$d"; done
cp "$DEV/requirements.txt" "$HERE/requirements.txt"
cp "$DEV/docs/DATA_FLOW.md" "$DEV/docs/SAFETY.md" "$HERE/docs/"
find "$HERE" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
echo "synced from $DEV"
