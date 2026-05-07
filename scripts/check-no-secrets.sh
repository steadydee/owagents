#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

patterns=(
  'sk-proj-[A-Za-z0-9_-]{20,}'
  'sk-[A-Za-z0-9_-]{20,}'
  'vcp_[A-Za-z0-9]{20,}'
  '[0-9]{8,}:[A-Za-z0-9_-]{30,}'
  '"private_key"[[:space:]]*:'
  '-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----'
  '"QUOTE_INTAKE_API_TOKEN"[[:space:]]*:[[:space:]]*"[A-Fa-f0-9]{32,}"'
  '"EXPENSE_INTAKE_API_TOKEN"[[:space:]]*:[[:space:]]*"[A-Fa-f0-9]{32,}"'
  'AIza[A-Za-z0-9_-]{20,}'
)

status=0
for pattern in "${patterns[@]}"; do
  if find . \
    -path './.git' -prune -o \
    -path './tools/owlswatch_quotes/tests' -prune -o \
    -type f -print0 |
    xargs -0 grep -E -n "$pattern" >/tmp/owlswatch-agent-secret-scan.txt 2>/dev/null; then
    echo "Potential secret match for pattern: $pattern" >&2
    sed 's/^/  /' /tmp/owlswatch-agent-secret-scan.txt >&2
    status=1
  fi
done

rm -f /tmp/owlswatch-agent-secret-scan.txt

if [ "$status" -ne 0 ]; then
  echo "Secret scan failed." >&2
  exit "$status"
fi

echo "Secret scan passed."

