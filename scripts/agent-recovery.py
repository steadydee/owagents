#!/usr/bin/env python3
"""Authenticated encrypted snapshots and isolated, non-booting restore drills."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import stat
import subprocess
import tempfile
import time
import zipfile

PROFILES = ("owlswatch", "hotel", "bailey-finance", "nomina")
MAGIC = b"OWLREC1\n"
EXCLUDE_DIRS = {".git", "node_modules", ".venv", "__pycache__", "tmp", "npm", "canvas", "plugin-skills", "extensions", "backups", "cache"}
MAX_BYTES = 10 * 1024 ** 3


class RecoveryError(Exception):
    pass


def private_dir(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise RecoveryError("UNSAFE_DIRECTORY")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RecoveryError("DIRECTORY_MUST_BE_OWNER_ONLY")
    return path.resolve()


def read_key(helper):
    try:
        result = subprocess.run([str(helper), "read"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RecoveryError("KEYCHAIN_UNAVAILABLE") from None
    if result.returncode or len(result.stdout) != 32:
        raise RecoveryError("RECOVERY_KEY_UNAVAILABLE")
    return result.stdout


def configured_workspaces(config):
    if not isinstance(config, dict) or not isinstance(config.get("agents"), dict):
        raise RecoveryError("INVALID_AGENT_INVENTORY")
    section = config["agents"]
    if "entries" in section:
        entries = section["entries"]
        if "list" in section or not isinstance(entries, dict):
            raise RecoveryError("INVALID_AGENT_INVENTORY")
        rows = list(entries.items())
    else:
        entries = section.get("list")
        if not isinstance(entries, list) or not all(isinstance(agent, dict) for agent in entries):
            raise RecoveryError("INVALID_AGENT_INVENTORY")
        rows = [(agent.get("id"), agent) for agent in entries]
    if not rows:
        raise RecoveryError("EMPTY_AGENT_INVENTORY")
    defaults = section.get("defaults", {})
    if not isinstance(defaults, dict):
        raise RecoveryError("INVALID_AGENT_INVENTORY")
    seen, workspaces = set(), []
    for identity, agent in rows:
        if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", identity) or identity in seen:
            raise RecoveryError("INVALID_AGENT_INVENTORY")
        if not isinstance(agent, dict) or agent.get("id", identity) != identity:
            raise RecoveryError("INVALID_AGENT_INVENTORY")
        seen.add(identity)
        workspace = agent.get("workspace", defaults.get("workspace"))
        if not isinstance(workspace, str) or not workspace:
            raise RecoveryError("WORKSPACE_UNAVAILABLE")
        workspaces.append(workspace)
    return workspaces


def configured_roots(home, profiles):
    roots = {}
    prefixes = {"owlswatch": "workspace-owlswatch", "hotel": "workspace-hotel", "bailey-finance": "workspace-bailey-finance", "nomina": "workspace-nomina"}
    for profile in profiles:
        if profile not in PROFILES:
            raise RecoveryError("EXCLUDED_PROFILE")
        state = home / (".openclaw-" + profile)
        if not state.is_dir() or state.is_symlink():
            raise RecoveryError("PROFILE_UNAVAILABLE")
        roots["profiles/" + profile] = state.resolve()
        config = json.loads((state / "openclaw.json").read_text())
        for configured in configured_workspaces(config):
            workspace = Path(str(home / configured[2:]) if configured.startswith("~/") else configured)
            if not workspace.is_absolute() or workspace.is_symlink() or not workspace.is_dir():
                raise RecoveryError("WORKSPACE_UNAVAILABLE")
            workspace = workspace.resolve()
            prefix = prefixes[profile]
            if (home / ".openclaw").is_symlink() or workspace.parent != (home / ".openclaw").resolve() or not (workspace.name == prefix or workspace.name.startswith(prefix + "-")):
                raise RecoveryError("WORKSPACE_OUTSIDE_APPROVED_PROFILE")
            roots["workspaces/" + workspace.name] = workspace
    return roots


def archive_snapshot(roots, archive, staging):
    manifest = {"version": 1, "createdAt": time.time(), "files": [], "skippedSymlinks": 0,
                "profiles": [name.split("/", 1)[1] for name in roots if name.startswith("profiles/")],
                "excludedDirectories": sorted(EXCLUDE_DIRS), "sqliteSnapshots": 0,
                "consistency": "Each SQLite database is an online backup. Cross-file/provider transactions require reconciliation before resuming writes."}
    total = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for prefix, root in sorted(roots.items()):
            for directory, dirs, files in os.walk(root, followlinks=False):
                manifest["skippedSymlinks"] += sum((Path(directory) / name).is_symlink() for name in dirs)
                dirs[:] = sorted(name for name in dirs if name not in EXCLUDE_DIRS and not (Path(directory) / name).is_symlink())
                for name in sorted(files):
                    source = Path(directory) / name
                    if source.is_symlink():
                        manifest["skippedSymlinks"] += 1
                        continue
                    if name.endswith(("-wal", "-shm", ".lock")):
                        continue
                    info = source.stat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    total += info.st_size
                    if total > MAX_BYTES:
                        raise RecoveryError("SNAPSHOT_SIZE_LIMIT")
                    relative = prefix + "/" + source.relative_to(root).as_posix()
                    snapshot = source
                    with source.open("rb") as stream:
                        sqlite = stream.read(16) == b"SQLite format 3\x00"
                    if sqlite:
                        snapshot = staging / (hashlib.sha256(relative.encode()).hexdigest() + ".sqlite")
                        src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
                        cold_snapshot = False
                        sidecars = [Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")]
                        try:
                            src.execute("PRAGMA schema_version").fetchone()
                        except sqlite3.OperationalError:
                            src.close()
                            # Renamed migration archives and closed WAL-mode
                            # databases can require immutable read-only access.
                            # Never ignore a live WAL or rollback journal.
                            if any(path.exists() for path in sidecars):
                                raise RecoveryError("SQLITE_SIDECAR_REQUIRES_OPERATOR_CHECK") from None
                            src = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
                            cold_snapshot = True
                        dst = sqlite3.connect(snapshot)
                        deadline = time.monotonic() + 30
                        def progress(status, remaining, pages):
                            if time.monotonic() > deadline:
                                raise RecoveryError("SQLITE_SNAPSHOT_BUSY")
                        try:
                            src.backup(dst, pages=128, progress=progress, sleep=.01)
                            dst.execute("PRAGMA journal_mode=DELETE")
                            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                                raise RecoveryError("SQLITE_INTEGRITY_FAILED")
                        finally:
                            dst.close()
                            src.close()
                        if cold_snapshot and (any(path.exists() for path in sidecars) or source.stat().st_mtime_ns != info.st_mtime_ns or source.stat().st_size != info.st_size):
                            raise RecoveryError("COLD_SQLITE_CHANGED_RETRY_SNAPSHOT")
                        manifest["sqliteSnapshots"] += 1
                    digest = hashlib.sha256()
                    with snapshot.open("rb") as stream, bundle.open(relative, "w") as target:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            target.write(chunk)
                    after = source.stat()
                    if not sqlite and (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                        raise RecoveryError("SOURCE_CHANGED_RETRY_SNAPSHOT")
                    manifest["files"].append({"path": relative, "sha256": digest.hexdigest(), "bytes": snapshot.stat().st_size,
                                              "mode": stat.S_IMODE(info.st_mode) & 0o700, "sqlite": sqlite})
                    if sqlite:
                        snapshot.unlink()
        bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
    return manifest


def encrypt(source, destination, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    nonce = secrets.token_bytes(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(MAGIC)
    with source.open("rb") as clear, destination.open("xb") as encrypted:
        os.chmod(destination, 0o600)
        encrypted.write(MAGIC + nonce)
        for chunk in iter(lambda: clear.read(1024 * 1024), b""):
            encrypted.write(cipher.update(chunk))
        encrypted.write(cipher.finalize())
        encrypted.write(cipher.tag)


def decrypt(source, destination, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.exceptions import InvalidTag
    size = source.stat().st_size
    if size > MAX_BYTES or size < len(MAGIC) + 12 + 16:
        raise RecoveryError("INVALID_ARCHIVE_SIZE")
    try:
        with source.open("rb") as encrypted, destination.open("xb") as clear:
            os.chmod(destination, 0o600)
            if encrypted.read(len(MAGIC)) != MAGIC:
                raise RecoveryError("INVALID_ARCHIVE_FORMAT")
            nonce = encrypted.read(12)
            encrypted.seek(-16, 2)
            tag = encrypted.read(16)
            encrypted.seek(len(MAGIC) + 12)
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            cipher.authenticate_additional_data(MAGIC)
            left = size - len(MAGIC) - 12 - 16
            while left:
                chunk = encrypted.read(min(left, 1024 * 1024))
                if not chunk:
                    raise RecoveryError("TRUNCATED_ARCHIVE")
                left -= len(chunk)
                clear.write(cipher.update(chunk))
            clear.write(cipher.finalize())
    except InvalidTag:
        raise RecoveryError("ARCHIVE_AUTHENTICATION_FAILED") from None


def restore_drill(archive, key, private_parent):
    with tempfile.TemporaryDirectory(prefix="restore-drill-", dir=private_parent) as directory:
        staging = Path(directory)
        clear = staging / "snapshot.zip"
        decrypt(archive, clear, key)
        target = staging / "isolated-restored-files"
        target.mkdir(mode=0o700)
        with zipfile.ZipFile(clear) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
                raise RecoveryError("INVALID_MANIFEST")
            names = [entry.get("path") for entry in manifest["files"]]
            if not all(isinstance(name, str) for name in names) or len(set(names)) != len(names) or len(bundle.namelist()) != len(names) + 1 or set(bundle.namelist()) != {*names, "manifest.json"}:
                raise RecoveryError("UNEXPECTED_ARCHIVE_MEMBERS")
            total = 0
            for entry in manifest["files"]:
                relative = PurePosixPath(entry["path"])
                if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 3 or relative.parts[0] not in {"profiles", "workspaces"}:
                    raise RecoveryError("UNSAFE_ARCHIVE_PATH")
                if relative.parts[0] == "profiles" and relative.parts[1] not in PROFILES:
                    raise RecoveryError("EXCLUDED_PROFILE")
                info = bundle.getinfo(str(relative))
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise RecoveryError("UNSAFE_ARCHIVE_LINK")
                total += info.file_size
                if total > MAX_BYTES:
                    raise RecoveryError("RESTORE_SIZE_LIMIT")
                path = target.joinpath(*relative.parts)
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                with bundle.open(info) as src, path.open("xb") as dst:
                    os.chmod(path, 0o600)
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        digest.update(chunk)
                        dst.write(chunk)
                if digest.hexdigest() != entry["sha256"] or path.stat().st_size != entry["bytes"]:
                    raise RecoveryError("RESTORE_HASH_MISMATCH")
                if entry.get("sqlite"):
                    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
                    try:
                        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise RecoveryError("RESTORE_SQLITE_INVALID")
                    finally:
                        db.close()
        restored_profiles = sorted({PurePosixPath(entry["path"]).parts[1] for entry in manifest["files"] if entry["path"].startswith("profiles/")})
        return {"status": "passed", "verifiedFiles": len(manifest["files"]), "verifiedBytes": total,
                "sqliteSnapshots": manifest["sqliteSnapshots"], "runtimeStarted": False, "externalMessages": False,
                "plaintextRestoreRemoved": True, "profiles": restored_profiles, "offDeviceRecoveryKeyVerified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "restore-drill", "export-key"))
    parser.add_argument("--keychain-helper", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, action="append")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--export-path", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    output = private_dir(args.output_dir)
    key = read_key(args.keychain_helper)
    if args.command == "export-key":
        if not args.export_path or not args.export_path.is_absolute():
            raise RecoveryError("EXPLICIT_ABSOLUTE_EXPORT_PATH_REQUIRED")
        with args.export_path.open("xb") as stream:
            os.chmod(args.export_path, 0o600)
            stream.write(key)
        print(json.dumps({"exported": str(args.export_path), "instruction": "Move this key to separately controlled offline storage. Hardware-loss recovery is unverified until that is done."}))
        return
    if args.command == "backup":
        roots = configured_roots(Path.home(), args.profile or PROFILES)
        if any(output == root or root in output.parents for root in roots.values()):
            raise RecoveryError("BACKUP_DESTINATION_INSIDE_SOURCE")
        filename = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4) + ".owlrec"
        archive = output / filename
        partial = archive.with_suffix(".partial")
        try:
            with tempfile.TemporaryDirectory(prefix="snapshot-", dir=output) as directory:
                staging = Path(directory)
                clear = staging / "snapshot.zip"
                manifest = archive_snapshot(roots, clear, staging)
                encrypt(clear, partial, key)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    else:
        if not args.archive:
            raise RecoveryError("ARCHIVE_REQUIRED")
        archive = args.archive.resolve()
    evidence = restore_drill(archive, key, output)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    evidence.update(archive=str(archive), at=time.time(), archiveSha256=digest.hexdigest())
    evidence_path = archive.with_suffix(".verification.json") if archive.parent == output else output / "restore-verification.json"
    evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n")
    print(json.dumps(evidence))


if __name__ == "__main__":
    try:
        main()
    except (RecoveryError, OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "failed", "code": str(error) if isinstance(error, RecoveryError) else type(error).__name__}))
        raise SystemExit(1)
