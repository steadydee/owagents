#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${PROFILE_DIR:-$HOME/.openclaw-owlswatch}"
CUENTA_WORKSPACE="${CUENTA_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch}"
COTIZA_WORKSPACE="${COTIZA_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-cotiza}"
CORREO_WORKSPACE="${CORREO_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-correo}"
COBROS_WORKSPACE="${COBROS_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-cobros}"
FINCA_PROFILE_DIR="${FINCA_PROFILE_DIR:-$HOME/.openclaw-finca}"
FINCA_WORKSPACE="${FINCA_WORKSPACE:-$HOME/.openclaw/workspace-finca-ops}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Backups/owlswatch-agents/runtime}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_ROOT/$STAMP"

mkdir -p "$OUT"

redact_json() {
  python3 - "$1" "$2" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
secret_words = ("token", "key", "secret", "credential", "password", "botToken", "OPENAI_API_KEY")
def redact(value, key=""):
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str) and any(word.lower() in key.lower() for word in secret_words):
        return "<redacted>"
    return value
with open(src) as f:
    data = json.load(f)
with open(dst, "w") as f:
    json.dump(redact(data), f, indent=2)
    f.write("\n")
PY
}

if [ -f "$PROFILE_DIR/openclaw.json" ]; then
  redact_json "$PROFILE_DIR/openclaw.json" "$OUT/openclaw.redacted.json"
fi
if [ -f "$FINCA_PROFILE_DIR/openclaw.json" ]; then
  redact_json "$FINCA_PROFILE_DIR/openclaw.json" "$OUT/finca-openclaw.redacted.json"
fi

mkdir -p "$OUT/cuenta" "$OUT/cotiza" "$OUT/correo" "$OUT/cobros" "$OUT/finca"
rsync -a --exclude 'memory' --exclude 'spool' --exclude '.openclaw' --exclude '.git' "$CUENTA_WORKSPACE/" "$OUT/cuenta/"
rsync -a --exclude 'memory' --exclude 'spool' --exclude 'mock' --exclude '.openclaw' "$COTIZA_WORKSPACE/" "$OUT/cotiza/"
rsync -a --exclude 'memory' --exclude 'tasks' --exclude '.openclaw' "$CORREO_WORKSPACE/" "$OUT/correo/" 2>/dev/null || true
rsync -a --exclude 'memory' --exclude '.openclaw' "$COBROS_WORKSPACE/" "$OUT/cobros/" 2>/dev/null || true
rsync -a --exclude 'memory' --exclude 'spool' --exclude 'mock' --exclude '.openclaw' "$FINCA_WORKSPACE/" "$OUT/finca/" 2>/dev/null || true

tar -C "$BACKUP_ROOT" -czf "$BACKUP_ROOT/$STAMP.tar.gz" "$STAMP"
rm -rf "$OUT"

echo "Runtime metadata backup written to $BACKUP_ROOT/$STAMP.tar.gz"
