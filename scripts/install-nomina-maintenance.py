#!/usr/bin/env python3
"""Install only Nomina maintenance and preserve private gateway error output."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import time


def atomic_private(path, data):
    if path.is_symlink():
        raise ValueError("Refusing a symlink installation target")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".nomina-install-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bootstrap_service(domain, path):
    # launchd can return EIO briefly after bootout while the old job exits.
    for attempt in range(4):
        result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True)
        if result.returncode == 0:
            return
        if result.returncode != 5 or attempt == 3:
            raise RuntimeError("Nomina launchd bootstrap failed; inspect the private service logs")
        time.sleep(0.5 * (2 ** attempt))


def reload_gateway(gateway_path):
    domain = "gui/" + str(os.getuid())
    target = domain + "/ai.openclaw.nomina"
    probe = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
    if probe.returncode == 0:
        subprocess.run(["launchctl", "bootout", target], check=True, capture_output=True)
    elif "Could not find service" not in probe.stderr:
        raise RuntimeError("Could not verify the existing Nomina service before reload")
    subprocess.run(["launchctl", "enable", target], check=True, capture_output=True)
    bootstrap_service(domain, gateway_path)


def install(workspace, home, openclaw, *, enable=False, activate=True, reload=False):
    workspace, home = Path(workspace).expanduser().resolve(), Path(home).expanduser().resolve()
    script = workspace / "tools/owlswatch_payroll/maintenance.py"
    python = workspace / ".venv/bin/python3"
    if not script.is_file() or not python.is_file():
        raise ValueError("Deploy the reviewed Nomina source and private Python environment first")
    profile = home / ".openclaw-nomina"
    logs = profile / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    if logs.is_symlink() or logs.stat().st_mode & 0o077:
        raise ValueError("Private non-symlink log directory required")
    plist_dir = home / "Library/LaunchAgents"
    gateway_path = plist_dir / "ai.openclaw.nomina.plist"
    gateway = plistlib.loads(gateway_path.read_bytes())
    if gateway.get("Label") != "ai.openclaw.nomina":
        raise ValueError("Unexpected gateway owner")
    gateway.update(StandardErrorPath=str(logs / "gateway.stderr.log"), RunAtLoad=True, KeepAlive=True)
    atomic_private(gateway_path, plistlib.dumps(gateway))
    for name in ("gateway.stderr.log", "maintenance.stdout.log", "maintenance.stderr.log"):
        path = logs / name
        if path.is_symlink():
            raise ValueError("Log paths cannot be symbolic links")
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
    label = "ai.openclaw.nomina.maintenance"
    target = plist_dir / (label + ".plist")
    plist = {"Label": label, "ProgramArguments": [str(python), "-B", str(script),
        "--profile-config", str(profile / "openclaw.json"), "--openclaw", str(openclaw)],
        "StartInterval": 300, "RunAtLoad": True, "ProcessType": "Background",
        "EnvironmentVariables": {"OWLSWATCH_PAYROLL_WORKSPACE": str(workspace),
            "PATH": str(Path(openclaw).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": str(logs / "maintenance.stdout.log"), "StandardErrorPath": str(logs / "maintenance.stderr.log")}
    atomic_private(target, plistlib.dumps(plist))
    if enable:
        atomic_private(workspace / "maintenance.enabled", b"enabled\n")
    if reload:
        reload_gateway(gateway_path)
    if activate:
        domain = "gui/" + str(os.getuid())
        subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True, check=False)
        subprocess.run(["launchctl", "enable", domain + "/" + label], check=True)
        bootstrap_service(domain, target)
    return {"label": label, "enabled": (workspace / "maintenance.enabled").exists(),
            "gateway_reload_required": not reload}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=os.environ.get("OWLSWATCH_PAYROLL_WORKSPACE", "~/.openclaw/workspace-nomina-nomina"))
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--reload-gateway", action="store_true")
    args = parser.parse_args()
    openclaw = shutil.which("openclaw")
    if not openclaw:
        raise SystemExit("OpenClaw executable was not found")
    print(json.dumps(install(args.workspace, Path.home(), openclaw, enable=args.enable,
                            activate=not args.no_activate, reload=args.reload_gateway)))
