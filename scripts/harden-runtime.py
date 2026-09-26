#!/usr/bin/env python3
"""Apply bounded runtime settings without replacing routing, models or secrets."""
import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("owlswatch", "hotel", "finca", "bailey-finance")
GUARD_ID = "owlswatch-runtime-guard"
PLUGIN_TRANSPORTS = {
    "owlswatch_intake": "owlswatch-intake", "owlswatch_quotes": "owlswatch-quotes",
    "owlswatch_email": "owlswatch-email", "owlswatch_cobros": "owlswatch-cobros",
    "registro_compliance": "registro-compliance", "hotel_pms": "hotel-pms",
}
LOOP_POLICY = {"enabled": True, "historySize": 30, "warningThreshold": 4,
               "criticalThreshold": 8, "globalCircuitBreakerThreshold": 12,
               "unknownToolThreshold": 3,
               "detectors": {"genericRepeat": True, "knownPollNoProgress": True, "pingPong": True}}


def harden(config, guard_path):
    result = copy.deepcopy(config)
    defaults = result.setdefault("agents", {}).setdefault("defaults", {})
    defaults.setdefault("heartbeat", {})["every"] = "0m"
    defaults["timeoutSeconds"] = min(defaults.get("timeoutSeconds", 600), 600)
    for agent in result["agents"].get("list", []):
        if "heartbeat" in agent:
            agent["heartbeat"]["every"] = "0m"
        if agent.get("id") == "correo":
            allowed = agent.get("tools", {}).get("alsoAllow", [])
            if allowed and "owlswatch_email_acknowledge_item" not in allowed:
                allowed.append("owlswatch_email_acknowledge_item")
    result.setdefault("tools", {})["loopDetection"] = copy.deepcopy(LOOP_POLICY)
    plugins = result.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {})
    entries[GUARD_ID] = {"enabled": True}
    paths = plugins.setdefault("load", {}).setdefault("paths", [])
    if guard_path not in paths:
        paths.append(guard_path)
    if isinstance(plugins.get("allow"), list) and GUARD_ID not in plugins["allow"]:
        plugins["allow"].append(GUARD_ID)
    for server_name, plugin_name in PLUGIN_TRANSPORTS.items():
        server = result.get("mcp", {}).get("servers", {}).get(server_name)
        if server and entries.get(plugin_name, {}).get("enabled") is True:
            # Keep the env section: native Python bridges resolve settings here.
            server["enabled"] = False
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".runtime-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    directory = Path.home() / (".openclaw-" + args.profile)
    path = directory / "openclaw.json"
    current = json.loads(path.read_text())
    destination = directory / "tools" / "agent_runtime_guard"
    candidate = harden(current, str(destination))
    if not args.apply:
        print(json.dumps({"profile": args.profile, "changeRequired": candidate != current,
                          "heartbeat": "disabled", "loopDetection": LOOP_POLICY,
                          "guard": GUARD_ID, "mode": "plan"}, indent=2))
        return
    subprocess.run([str(ROOT / "scripts/assert-release-ready.sh")], check=True)
    shutil.copytree(ROOT / "tools/agent_runtime_guard", destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("tests", "node_modules", "__pycache__"))
    package = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, check=True).stdout.strip()
    sdk_link = destination / "node_modules/openclaw"
    sdk_link.parent.mkdir(exist_ok=True)
    if sdk_link.is_symlink():
        sdk_link.unlink()
    if not sdk_link.exists():
        sdk_link.symlink_to(Path(package) / "openclaw", target_is_directory=True)
    temp = directory / ".hardening-candidate.json"
    atomic_json(temp, candidate)
    try:
        env = {**os.environ, "OPENCLAW_CONFIG_PATH": str(temp), "OPENCLAW_STATE_DIR": str(directory)}
        validation = subprocess.run(["openclaw", "--profile", args.profile, "config", "validate"], env=env, capture_output=True, text=True)
        if validation.returncode:
            raise SystemExit("Candidate validation failed; live configuration was not changed. Inspect the local candidate with OpenClaw.")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = directory / "backups" / ("before-hardening-" + stamp + ".json")
        atomic_json(backup, current)
        atomic_json(path, candidate)
        # Older config backups may contain the same credentials as the live file.
        # Preserve their contents and retention, but remove other-user access.
        for saved in directory.glob("openclaw.json*"):
            if saved.is_file() and not saved.is_symlink():
                saved.chmod(0o600)
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        atomic_json(directory / "runtime-release.json", {"sha": sha, "profile": args.profile, "configuredAt": stamp, "backup": str(backup)})
        print(json.dumps({"profile": args.profile, "applied": True, "sha": sha, "backup": str(backup)}))
    finally:
        temp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
