#!/usr/bin/env python3
"""Stage an immutable runtime and a private migration plan; never activate it.

10.5 changes config/auth/state schemas. Activation intentionally fails closed
until profile migration and full-state rollback have been independently proven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

NODE = "24.22.1"
OPENCLAW = "2026.10.5"
DEEPSEEK_PACKAGE = "@openclaw/deepseek-provider"
DEEPSEEK_INTEGRITY = "sha512-m+pf1G9P6UlGj+vMHBESy5fpQSccr9K1/As/jow/qhki9oE9DJOuwA7dcEFqELdLEIpdWsJg1tt9skcXwUef9A=="
PROFILES = {
    "owlswatch": ("gui", ["", ".telegram-observer", ".email-poll", ".email-daily-summary", ".email-unanswered"],
                  ["workspace-owlswatch", "workspace-owlswatch-main", "workspace-owlswatch-cotiza", "workspace-owlswatch-correo", "workspace-owlswatch-cobros", "workspace-owlswatch-registro"]),
    "hotel": ("system", ["", ".telegram-observer", ".daily-summary", ".registro-pickup"], ["workspace-hotel-ops"]),
    "bailey-finance": ("gui", ["", ".telegram-observer"], ["workspace-bailey-finance"]),
    "nomina": ("gui", ["", ".maintenance"], ["workspace-nomina-nomina"]),
}


class Refusal(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_profile(profile):
    if profile not in PROFILES:
        raise Refusal("Profile is outside the four authorized domains.")


def contained(path, roots):
    resolved = path.resolve()
    if not any(resolved == r.resolve() or r.resolve() in resolved.parents for r in roots):
        raise Refusal("Configured path escapes the selected profile's approved roots.")
    return resolved


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise Refusal("Required file is missing or is an unexpected symlink.")
    return path


def tree_manifest(root):
    """Hash files and reject package links that escape the approved bundle."""
    if root.is_symlink() or not root.is_dir():
        raise Refusal("Runtime tree must be a real directory.")
    entries = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            target = os.readlink(path)
            if os.path.isabs(target) or not path.exists():
                raise Refusal("Runtime bundle contains an absolute or dangling symlink.")
            contained(path, [root])
            entries[key] = {"symlink": target}
        elif path.is_file():
            entries[key] = {"sha256": sha(path), "executable": bool(path.stat().st_mode & 0o111)}
        elif not path.is_dir():
            raise Refusal("Runtime bundle contains a special file.")
    return entries


def manifest_digest(entries):
    return digest(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode())


def inspect_candidate(candidate):
    candidate = candidate.resolve()
    if any(part.startswith(".openclaw") or part == ".npm-global" for part in candidate.parts):
        raise Refusal("Candidate must be separate from live and shared runtime directories.")
    node = regular(candidate / f"node-v{NODE}-darwin-arm64/bin/node")
    contained(node, [candidate])
    npm = candidate / f"openclaw-{OPENCLAW}"
    package = npm / "node_modules/openclaw"
    contained(package, [candidate])
    if package.is_symlink():
        raise Refusal("Runtime package may not be a symlink.")
    version = json.loads(regular(package / "package.json").read_text()).get("version")
    lock = json.loads(regular(npm / "package-lock.json").read_text())
    locked = lock.get("packages", {}).get("node_modules/openclaw", {})
    if version != OPENCLAW or locked.get("version") != OPENCLAW or not str(locked.get("integrity", "")).startswith("sha512-"):
        raise Refusal("Expected exact OpenClaw version and npm integrity record.")
    regular(package / "openclaw.mjs")
    regular(package / "dist/index.js")
    for name, package_name in (("openai", "@openclaw/openai-provider"), ("telegram", "@openclaw/telegram")):
        bundled = package / "dist/extensions" / name
        meta = json.loads(regular(bundled / "package.json").read_text())
        manifest = json.loads(regular(bundled / "openclaw.plugin.json").read_text())
        if meta.get("name") != package_name or meta.get("version") != OPENCLAW or manifest.get("id") != name:
            raise Refusal("Required bundled OpenAI/Telegram plugin metadata differs from the approved release.")
        regular(bundled / "index.js")
    files = tree_manifest(npm / "node_modules")
    provider_npm = candidate / f"provider-plugins-{OPENCLAW}"
    provider = provider_npm / "node_modules" / DEEPSEEK_PACKAGE
    contained(provider, [candidate])
    provider_meta = json.loads(regular(provider / "package.json").read_text())
    provider_manifest = json.loads(regular(provider / "openclaw.plugin.json").read_text())
    provider_lock = json.loads(regular(provider_npm / "package-lock.json").read_text())
    provider_locked = provider_lock.get("packages", {}).get("node_modules/" + DEEPSEEK_PACKAGE, {})
    if (provider_meta.get("name") != DEEPSEEK_PACKAGE or provider_meta.get("version") != OPENCLAW
            or provider_locked.get("version") != OPENCLAW or provider_locked.get("integrity") != DEEPSEEK_INTEGRITY
            or provider_manifest.get("id") != "deepseek"
            or provider_meta.get("openclaw", {}).get("compat", {}).get("pluginApi") != ">=" + OPENCLAW):
        raise Refusal("External DeepSeek provider does not match the pinned official package/version/integrity.")
    regular(provider / "dist/index.js")
    provider_files = tree_manifest(provider)
    core_digest = manifest_digest(files)
    if any(key == DEEPSEEK_PACKAGE or key.startswith(DEEPSEEK_PACKAGE + "/") for key in files):
        raise Refusal("Core bundle already contains an external provider; reconcile package ownership.")
    for relative, value in provider_files.items():
        target = DEEPSEEK_PACKAGE + "/" + relative
        if target in files:
            raise Refusal("Core bundle already contains an external provider; reconcile package ownership.")
        files[target] = value
    return {"node": node, "modules": npm / "node_modules", "package": package,
            "nodeSha256": sha(node), "npmTreeSha256": manifest_digest(files), "files": files,
            "npmIntegrity": locked["integrity"], "coreNpmTreeSha256": core_digest,
            "provider": provider, "providerTreeSha256": manifest_digest(provider_files),
            "providerIntegrity": provider_locked["integrity"]}


def profile_paths(home, profile):
    require_profile(profile)
    if home.is_symlink():
        raise Refusal("Home must be a canonical directory.")
    home = home.resolve()
    state = home / f".openclaw-{profile}"
    if state.is_symlink() or not state.is_dir():
        raise Refusal("Profile state must exist as a real directory.")
    owner, suffixes, workspaces = PROFILES[profile]
    workspace_roots = [home / ".openclaw" / w for w in workspaces]
    if (home / ".openclaw").is_symlink() or any(p.is_symlink() for p in workspace_roots):
        raise Refusal("Approved workspace roots may not be symlinks to another domain.")
    return state, owner, suffixes, [state, *workspace_roots]


def scoped_cli(node, entry, state, profile):
    # The wrapper permits jobs' existing redundant --profile flag only when it
    # matches this domain. It forcibly owns state/config env; no global updates.
    constants = repr({"node": str(node), "entry": str(entry), "state": str(state), "profile": profile})
    return ('''#!/usr/bin/python3
import os, sys
c = ''' + constants + '''
args = []; incoming = iter(sys.argv[1:])
for arg in incoming:
    if arg == "--profile":
        if next(incoming, None) != c["profile"]: sys.exit("Wrong profile for scoped CLI")
    elif arg.startswith("--profile="):
        if arg.split("=", 1)[1] != c["profile"]: sys.exit("Wrong profile for scoped CLI")
    elif arg in ("--dev", "--config", "--state-dir") or arg.startswith(("--config=", "--state-dir=")):
        sys.exit("State override forbidden in scoped CLI")
    else: args.append(arg)
if not args or args[0] in ("update", "install", "uninstall", "onboard"):
    sys.exit("Use the gated release procedure for installation changes")
os.environ.update(OPENCLAW_PROFILE=c["profile"], OPENCLAW_STATE_DIR=c["state"], OPENCLAW_CONFIG_PATH=c["state"]+"/openclaw.json")
os.environ["PATH"] = os.path.dirname(c["node"]) + ":" + os.environ.get("PATH", "")
os.execv(c["node"], [c["node"], c["entry"], "--profile", c["profile"], *args])
''').encode()


def build_plan(home, profile, candidate, system_dir=Path("/Library/LaunchDaemons")):
    state, owner, suffixes, approved = profile_paths(home, profile)
    info = inspect_candidate(candidate)
    generation = f"node-{NODE}-openclaw-{OPENCLAW}-{info['npmTreeSha256'][:12]}-{info['nodeSha256'][:12]}"
    release = state / "runtime/releases" / generation
    node = release / "node/bin/node"
    package = release / "node_modules/openclaw"
    cli = release / "bin/openclaw-profile"
    uid = home.stat().st_uid
    domain = "system" if owner == "system" else f"gui/{uid}"
    plist_dir = system_dir if owner == "system" else home / "Library/LaunchAgents"
    config_path = regular(state / "openclaw.json")
    config = json.loads(config_path.read_text())
    gates = ["Exact source release must be clean main == freshly fetched origin/main.",
             "Verify pinned Node provenance and approved complete npm-tree digest, including npm lifecycle-script availability.",
             "Validate all bundled providers and each configured plugin against this runtime in disposable profile copies.",
             "Add the pinned external DeepSeek package root to migrated plugins.load.paths and retain explicit provider grants; validate provider discovery.",
             "Migrate disposable config/auth/state and compare routing, tool grants, secrets, queues and schedules before/after.",
             "Quiesce only this profile's jobs and gateway; make an encrypted full-state checkpoint and prove restore to the old runtime.",
             "Record launchd loaded/disabled state; never enable previously disabled jobs or Hotel GUI duplicates.",
             "Verify profile-only no-delivery canary and authenticated local gateway readiness before restarting eligible schedules."]
    if "list" in config.get("agents", {}): gates.append("Legacy agents.list requires reviewed agents.entries migration.")
    if "models" in config.get("agents", {}).get("defaults", {}): gates.append("Legacy agents.defaults.models requires modelPolicy.allow migration.")
    auth_files = list((state / "agents").glob("*/agent/auth-profiles.json"))
    if auth_files: gates.append("Legacy auth-profiles.json exists; migrate and test SQLite-backed auth in a disposable profile copy.")
    operations = []
    snapshots = {}
    for suffix in suffixes:
        label = f"ai.openclaw.{profile}{suffix}"
        path = plist_dir / f"{label}.plist"
        if not path.exists():
            if not suffix: raise Refusal("Expected gateway supervisor plist is missing.")
            continue
        raw = regular(path).read_bytes()
        data = plistlib.loads(raw)
        if data.get("Label") != label: raise Refusal("Supervisor label does not match its scoped path.")
        args = data.get("ProgramArguments", [])
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args): raise Refusal("Unrecognized supervisor arguments.")
        if data.get("Program") and (not args or data["Program"] != args[0]):
            raise Refusal("Supervisor Program differs from its argument executable.")
        if not suffix:
            old_entry = str(home / ".npm-global/lib/node_modules/openclaw/dist/index.js")
            if args.count(old_entry) != 1: raise Refusal("Gateway runtime invocation needs explicit review.")
            index = args.index(old_entry)
            if index < 2 or args[index + 1:index + 2] != ["gateway"]: raise Refusal("Gateway wrapper invocation needs explicit review.")
            # Preserve all wrapper, env-file, gateway and port arguments.
            args[index - 1:index + 1] = [str(node), str(package / "dist/index.js")]
        else:
            shared = str(home / ".npm-global/bin/openclaw")
            args = [str(cli) if arg == shared else arg for arg in args]
            data.setdefault("EnvironmentVariables", {})["OPENCLAW_BIN"] = str(cli)
        data["ProgramArguments"] = args
        snapshots[label] = {"original": raw, "proposed": plistlib.dumps(data)}
        operations.append({"kind": "replace_plist_after_gates", "path": str(path), "service": f"{domain}/{label}", "beforeSha256": digest(raw), "snapshot": label})
    for item in config.get("plugins", {}).get("load", {}).get("paths", []):
        expanded = str(item).replace("~/", str(home) + "/", 1) if str(item).startswith("~/") else str(item)
        path = contained(Path(expanded), approved)
        directory = path.parent if path.is_file() else path
        if not directory.is_dir(): raise Refusal("Configured plugin path is missing.")
        link = directory / "node_modules/openclaw"
        contained(link.parent, approved)
        if link.exists() and not link.is_symlink(): raise Refusal("Plugin contains a real OpenClaw installation; cannot replace it automatically.")
        previous = os.readlink(link) if link.is_symlink() else None
        operations.append({"kind": "replace_sdk_symlink_after_gates", "path": str(link), "beforeTarget": previous, "afterTarget": str(package)})
    active_path = state / "runtime/active.json"
    contained(active_path.parent, [state])
    if active_path.exists() or active_path.is_symlink():
        regular(active_path)
        if active_path.stat().st_mode & 0o077:
            raise Refusal("Existing runtime selection metadata must be private.")
    active = {"schemaVersion": 1, "profile": profile, "nodePath": str(node), "openclawPackage": str(package)}
    operations.append({"kind": "publish_runtime_selection_after_gates", "path": str(active_path),
                       "beforeSha256": sha(active_path) if active_path.exists() else None, "after": active,
                       "mode": "0600", "rollback": "Restore exact prior bytes or absence; never infer a global runtime."})
    return {"profile": profile, "launchdDomain": domain, "ownerUid": uid, "runtimeRelease": str(release),
            "nodeVersion": NODE, "openclawVersion": OPENCLAW, "nodeSha256": info["nodeSha256"],
            "npmTreeSha256": info["npmTreeSha256"], "npmIntegrity": info["npmIntegrity"],
            "coreNpmTreeSha256": info["coreNpmTreeSha256"],
            "externalProviders": [{"name": DEEPSEEK_PACKAGE, "version": OPENCLAW, "integrity": info["providerIntegrity"],
                                   "treeSha256": info["providerTreeSha256"], "loadPath": str(release / "node_modules" / DEEPSEEK_PACKAGE),
                                   "entry": str(release / "node_modules" / DEEPSEEK_PACKAGE / "dist/index.js")}],
            "activeRuntime": active,
            "configSha256": sha(config_path), "activationReady": False, "requiredGates": gates,
            "operations": operations, "scopedCli": str(cli),
            "untouchedHotelGuiPlists": [str(home / "Library/LaunchAgents" / f"ai.openclaw.hotel{s}.plist") for s in suffixes] if profile == "hotel" else [],
            "rollback": "Before boot, restore exact plist bytes and SDK link targets. After any 10.5 boot, also restore the tested full-state checkpoint; file-only rollback is unsafe."}, snapshots, info


def release_gate():
    root = Path(__file__).resolve().parents[1]
    try:
        subprocess.run([str(root / "scripts/assert-release-ready.sh")], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        raise Refusal("Release gate failed; nothing was installed. Publish clean exact main first.") from exc


def stage(plan, snapshots, info, *, node_sha256, tree_sha256, npm_integrity):
    release_gate()
    if (node_sha256, tree_sha256, npm_integrity) != (info["nodeSha256"], info["npmTreeSha256"], info["npmIntegrity"]):
        raise Refusal("Candidate does not match approved hashes/integrity.")
    verify_node(info["node"])
    destination = Path(plan["runtimeRelease"])
    state = destination.parents[2]
    generated = {
        "bin/openclaw-profile": (scoped_cli(destination / "node/bin/node", destination / "node_modules/openclaw/openclaw.mjs", state, plan["profile"]), 0o700),
        "activation-plan.json": ((json.dumps(plan, indent=2) + "\n").encode(), 0o600),
        "runtime-selection.proposed.json": ((json.dumps(plan["activeRuntime"], indent=2) + "\n").encode(), 0o600),
    }
    for label, data in snapshots.items():
        for kind, raw in data.items():
            generated[f"snapshots/{label}.{kind}.plist"] = (raw, 0o600)
    contained(destination.parent, [state])
    for directory in [state / "runtime", state / "runtime/releases"]:
        if directory.is_symlink(): raise Refusal("Runtime destination may not be a symlink.")
        directory.mkdir(mode=0o700, exist_ok=True)
    if destination.exists():
        if destination.is_symlink(): raise Refusal("Existing release may not be a symlink.")
        if sha(destination / "node/bin/node") != node_sha256 or manifest_digest(tree_manifest(destination / "node_modules")) != tree_sha256:
            raise Refusal("Existing immutable release differs; refusing overwrite.")
        for relative, (raw, mode) in generated.items():
            existing = regular(destination / relative)
            if existing.read_bytes() != raw or existing.stat().st_mode & 0o777 != mode:
                raise Refusal("Immutable staging files or profile plan changed; refusing overwrite.")
        return "already_staged"
    temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=destination.parent))
    try:
        (temporary / "node/bin").mkdir(parents=True)
        shutil.copy2(info["node"], temporary / "node/bin/node")
        shutil.copytree(info["modules"], temporary / "node_modules", symlinks=True)
        provider_target = temporary / "node_modules" / DEEPSEEK_PACKAGE
        provider_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(info["provider"], provider_target, symlinks=True)
        if sha(temporary / "node/bin/node") != node_sha256 or manifest_digest(tree_manifest(temporary / "node_modules")) != tree_sha256:
            raise Refusal("Copied runtime failed integrity verification.")
        for relative, (raw, mode) in generated.items():
            path = temporary / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(raw); path.chmod(mode)
        os.rename(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return "staged_not_activated"


def verify_node(node):
    """Execute only the explicitly approved binary, without inherited Node hooks."""
    try:
        result = subprocess.run(
            [str(node), "--eval", "process.stdout.write(JSON.stringify([process.versions.node,process.platform,process.arch]))"],
            env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True, timeout=10)
        if json.loads(result.stdout) != [NODE, "darwin", "arm64"]:
            raise Refusal("Approved Node binary reports an unexpected version or platform.")
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise Refusal("Approved Node binary could not be verified.") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "stage", "activate"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--node-sha256")
    parser.add_argument("--npm-tree-sha256")
    parser.add_argument("--npm-integrity")
    args = parser.parse_args()
    try:
        require_profile(args.profile)
        if args.command == "activate":
            raise Refusal("Activation unavailable: prove profile migration, provider/plugin readiness and full-state rollback first. No live files or services were changed.")
        plan, snapshots, info = build_plan(args.home, args.profile, args.candidate_root)
        if args.command == "stage":
            result = stage(plan, snapshots, info, node_sha256=args.node_sha256,
                           tree_sha256=args.npm_tree_sha256, npm_integrity=args.npm_integrity)
            print(json.dumps({"status": result, "path": plan["runtimeRelease"], "activationReady": False}))
        else:
            print(json.dumps(plan, indent=2))
    except (Refusal, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
