#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.npm-global/bin:/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
CORREO_WORKSPACE="${OWLSWATCH_EMAIL_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-correo}"
PYTHON_BIN="${OWLSWATCH_EMAIL_PYTHON:-python3}"
exec "$PYTHON_BIN" "$CORREO_WORKSPACE/tools/owlswatch_email/run.py" daily_summary "$@"
