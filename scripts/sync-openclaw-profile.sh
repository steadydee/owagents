#!/usr/bin/env bash
set -euo pipefail

echo "This repo intentionally does not sync the live openclaw.json into git."
echo "Use openclaw/profiles/owlswatch/openclaw.example.json as the source template."
echo "For a redacted runtime snapshot, run: ./scripts/backup-runtime-metadata.sh"

