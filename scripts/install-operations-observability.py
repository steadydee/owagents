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
FILES = ("operations-observability.py", "check-deepseek-balance.mjs", "monitor-http-budget.py", "agent-health.py", "agent-recovery.py", "recovery-keychain.swift")


def job_specs(home, python):
    target = home / "Library/Application Support/OwlswatchOperations"
    binary = target / "bin"
    monitor_args = [python, str(binary / "operations-observability.py"), "--output-dir", str(target / "report"),
                    "--budget-state", str(target / "monitor-state/http-budget.sqlite3"),
                    "--profile", "owlswatch", "--profile", "hotel", "--profile", "bailey-finance"]
    return {
        "ai.openclaw.operations-observability": {
            "ProgramArguments": monitor_args, "StartInterval": 300, "RunAtLoad": True,
        },
        "ai.openclaw.operations-balance-daily": {
            "ProgramArguments": monitor_args + ["--check-balance"],
            "StartCalendarInterval": {"Hour": 8, "Minute": 15}, "RunAtLoad": False,
        },
        "ai.openclaw.operations-recovery": {
            "ProgramArguments": [python, str(binary / "agent-recovery.py"), "backup", "--keychain-helper", str(binary / "recovery-keychain"),
                                 "--output-dir", str(home / "Backups/owlswatch-agents/encrypted-runtime")],
            "StartInterval": 86400, "RunAtLoad": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--monitor-only", action="store_true", help="Update reporting and budget jobs without changing backup/keychain services")
    args = parser.parse_args()
    home = Path.home()
    target = home / "Library/Application Support/OwlswatchOperations"
    binary = target / "bin"
    python = sys.executable
    jobs = job_specs(home, python)
    files = FILES
    if args.monitor_only:
        jobs.pop("ai.openclaw.operations-recovery")
        files = tuple(name for name in FILES if name not in {"agent-recovery.py", "recovery-keychain.swift"})
    if not args.install:
        print(json.dumps({"mode": "plan", "profiles": ["owlswatch", "hotel", "bailey-finance"], "pausedProfiles": ["nomina"], "jobs": jobs,
                          "messages": False, "gatewayRestarts": False, "automaticKeyExport": False,
                          "scheduledVercelProbes": 0, "scheduledModelProbes": 0,
                          "balanceAttemptsPerRolling24Hours": 3}, indent=2))
        return
    subprocess.run([str(ROOT / "scripts/assert-release-ready.sh")], cwd=ROOT, check=True)
    if not args.monitor_only:
        subprocess.run([python, "-c", "from cryptography.hazmat.primitives.ciphers import Cipher"], check=True)
    os.umask(0o077)
    binary.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    binary.chmod(0o700)
    for name in files:
        shutil.copyfile(ROOT / "scripts" / name, binary / name)
    budget_dir = target / "monitor-state"
    if budget_dir.is_symlink():
        raise ValueError("Private non-symlink budget directory required")
    budget_dir.mkdir(exist_ok=True, mode=0o700)
    budget_dir.chmod(0o700)
    # init validates existing state and never resets consumed permits on upgrade.
    initialized = subprocess.run([python, str(binary / "monitor-http-budget.py"), "init", "--state", str(budget_dir / "http-budget.sqlite3")],
                                 check=True, capture_output=True, text=True)
    if json.loads(initialized.stdout).get("status") != "initialized":
        raise RuntimeError("Monitoring budget is unavailable; preserve its existing files and inspect before activation")
    if not args.monitor_only:
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
    release_record = "monitoring-source-release.json" if args.monitor_only else "source-release.json"
    (target / release_record).write_text(json.dumps({"gitSha": commit, "scripts": list(files)}) + "\n")
    print(json.dumps({"installed": list(jobs), "report": str(target / "report/index.html"), "gitSha": commit}))


if __name__ == "__main__":
    main()
