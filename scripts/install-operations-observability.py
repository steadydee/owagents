#!/usr/bin/env python3
"""Install fixed, non-model monitoring/backup jobs from a clean main release."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = ("operations-observability.py", "check-deepseek-balance.mjs", "agent-health.py", "agent-recovery.py", "recovery-keychain.swift")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    target = home / "Library/Application Support/OwlswatchOperations"
    binary = target / "bin"
    python = sys.executable
    jobs = {
        "ai.openclaw.operations-observability": {
            "ProgramArguments": [python, str(binary / "operations-observability.py"), "--output-dir", str(target / "report"), "--check-balance"],
            "StartInterval": 300, "RunAtLoad": True,
        },
        "ai.openclaw.operations-recovery": {
            "ProgramArguments": [python, str(binary / "agent-recovery.py"), "backup", "--keychain-helper", str(binary / "recovery-keychain"),
                                 "--output-dir", str(home / "Backups/owlswatch-agents/encrypted-runtime")],
            "StartInterval": 86400, "RunAtLoad": False,
        },
    }
    if not args.install:
        print(json.dumps({"mode": "plan", "profiles": ["owlswatch", "hotel", "bailey-finance", "nomina"], "jobs": jobs,
                          "messages": False, "gatewayRestarts": False, "automaticKeyExport": False}, indent=2))
        return
    subprocess.run([str(ROOT / "scripts/assert-release-ready.sh")], cwd=ROOT, check=True)
    subprocess.run([python, "-c", "from cryptography.hazmat.primitives.ciphers import Cipher"], check=True)
    os.umask(0o077)
    binary.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    binary.chmod(0o700)
    for name in FILES:
        shutil.copyfile(ROOT / "scripts" / name, binary / name)
    subprocess.run(["swiftc", "-module-cache-path", str(target / "swift-cache"), str(binary / "recovery-keychain.swift"), "-o", str(binary / "recovery-keychain")], check=True)
    subprocess.run([str(binary / "recovery-keychain"), "ensure"], check=True)
    launch_agents = home / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPYCACHEPREFIX": str(target / "pycache")}
    for label, values in jobs.items():
        job = {"Label": label, **values, "EnvironmentVariables": environment,
               "StandardOutPath": str(target / (label + ".stdout.log")), "StandardErrorPath": str(target / (label + ".stderr.log"))}
        path = launch_agents / (label + ".plist")
        path.write_bytes(plistlib.dumps(job))
        path.chmod(0o600)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        subprocess.run(["launchctl", "enable", f"gui/{os.getuid()}/{label}"], check=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (target / "source-release.json").write_text(json.dumps({"gitSha": commit, "scripts": list(FILES)}) + "\n")
    print(json.dumps({"installed": list(jobs), "report": str(target / "report/index.html"), "gitSha": commit}))


if __name__ == "__main__":
    main()
