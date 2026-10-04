#!/usr/bin/env python3
"""Record deployed source hashes without copying runtime state or credentials."""
import argparse
import datetime as dt
import hashlib
import importlib.util
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hardening", ROOT / "scripts/harden-runtime.py")
hardening = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardening)

parser = argparse.ArgumentParser()
parser.add_argument("--profile", choices=["owlswatch", "hotel", "finca", "nomina"], required=True)
parser.add_argument("--workspace", action="append", required=True)
parser.add_argument("--staged", action="store_true", help="Record installed source only; do not claim a live deployment")
args = parser.parse_args()
entries = {}
for item in args.workspace:
    workspace = Path(item).expanduser()
    for path in workspace.rglob("*"):
        rel = path.relative_to(workspace)
        if any(part in {"spool", "state", "memory", "tasks", "node_modules", "__pycache__", "secrets", "data", ".git", ".venv", "exports", "backups"} for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink() and path.suffix in {".py", ".js", ".mjs", ".md", ".json"} and path.name not in {"USER.md", "MEMORY.md", "payroll-config.json"}:
            entries[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
destination = Path.home() / (".openclaw-" + args.profile) / ("source-staged.json" if args.staged else "source-release.json")
hardening.atomic_json(destination, {"sha": sha, "status": "staged" if args.staged else "deployed",
                                   ("stagedAt" if args.staged else "deployedAt"): dt.datetime.now(dt.timezone.utc).isoformat(), "files": entries})
print(f"Recorded {args.profile} {'staged source' if args.staged else 'source release'} {sha}: {len(entries)} files")
