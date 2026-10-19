"""Explicit Linux deployment steps. No remote access, start, reset or cleanup.

Run with Python -B. Mutating commands are Linux/root-only, fixed-path and
create-or-verify; failed partial releases remain for investigation, never reused.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import grp
import re
import shutil
import stat
import subprocess
import tarfile
from config import ConfigError
from artifact_permissions import ArtifactError, scan_tree, remove_write_bits

SOURCE = Path("/home/admin/apps/trippilot")
BASE = Path("/opt/trippilot")
RELEASES = BASE / "releases"
CONFIG = Path("/etc/trippilot")
DATA = Path("/var/lib/trippilot")
BACKUPS = Path("/var/backups/trippilot")
UV = Path("/usr/local/bin/trippilot-uv")
NGINX = Path("/etc/nginx")
CERTIFICATES = Path("/etc/letsencrypt/live/trip.zijinglab.com")
SHA = re.compile(r"[0-9a-f]{40}")
PROTECTED_CACHE = {"backend/app/__pycache__/config.cpython-313.pyc", "backend/app/integrations/__pycache__/amap.cpython-313.pyc"}


class Refused(Exception):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused(code)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, capture: bool = False) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env if env is not None else clean_environment(), check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, umask=0o022)
    return result.stdout.strip() if capture else ""


def clean_environment() -> dict[str, str]:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root", "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "UV_CACHE_DIR": "/var/cache/trippilot/uv",
            "UV_PYTHON_INSTALL_DIR": str(BASE / "python"), "UV_PYTHON_INSTALL_BIN": "false",
            "NEXT_TELEMETRY_DISABLED": "1"}


def git_command(*arguments: str) -> list[str]:
    # Root reads the explicitly trusted admin clone; never edit global Git config.
    return ["git", "-c", f"safe.directory={SOURCE}", *arguments]


def linux() -> None:
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "ubuntu_x64_required")
    values = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    require(values.get("ID", "").strip('"') == "ubuntu" and values.get("VERSION_ID", "").strip('"') == "24.04", "ubuntu_24_04_required")


def root() -> None:
    linux()
    require(os.geteuid() == 0, "sudo_required")


def deployment_lock(path: Path = Path("/run/lock/trippilot-deploy.lock")) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, "deployment_lock_permissions")
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Refused("another_deployment_step_is_running") from None
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def checked_release(commit: str) -> Path:
    require(SHA.fullmatch(commit) is not None, "full_commit_required")
    target = RELEASES / commit
    require(not target.is_symlink(), "release_symlink_refused")
    return target


def exclusive_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def ensure_directory(path: Path, uid: int, gid: int, mode: int) -> None:
    require(not path.is_symlink(), "directory_symlink_refused")
    if path.exists():
        info = path.stat()
        require(path.is_dir() and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode), "existing_directory_permissions_differ")
        return
    path.mkdir(mode=mode)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def install_new_or_identical(source: Path, target: Path, mode: int, gid: int = 0) -> None:
    require(not target.is_symlink(), "file_symlink_refused")
    if target.exists():
        info = target.stat()
        require(target.is_file() and digest(source) == digest(target)
                and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, gid, mode), "existing_file_differs_stop_and_review")
        return
    with target.open("xb") as output, source.open("rb") as input_stream:
        os.fchmod(output.fileno(), mode)
        shutil.copyfileobj(input_stream, output)
        os.fchown(output.fileno(), 0, gid)


def service_identity(name: str) -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        run(["/usr/sbin/useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", name])
        account = pwd.getpwnam(name)
    require(0 < account.pw_uid < 1000 and 0 < account.pw_gid < 1000 and account.pw_dir == "/nonexistent" and account.pw_shell == "/usr/sbin/nologin"
            and grp.getgrgid(account.pw_gid).gr_name == name
            and os.getgrouplist(name, account.pw_gid) == [account.pw_gid], "existing_service_identity_differs")
    return account


def layout() -> None:
    api, web = service_identity("trippilot-api"), service_identity("trippilot-web")
    require(api.pw_uid != web.pw_uid and api.pw_gid != web.pw_gid, "separate_service_identities_required")
    for path, uid, gid, mode in ((BASE, 0, 0, 0o755), (RELEASES, 0, 0, 0o755),
                               (CONFIG, 0, 0, 0o755), (DATA, api.pw_uid, api.pw_gid, 0o700),
                               (BACKUPS, 0, 0, 0o700), (BASE / "receipts", 0, 0, 0o700), (Path("/var/cache/trippilot"), 0, 0, 0o700),
                               (Path("/var/cache/trippilot/uv"), 0, 0, 0o700),
                               (Path("/var/www/trippilot-acme"), 0, 0, 0o755)):
        ensure_directory(path, uid, gid, mode)
    print("Layout and separate service identities verified; no application started.")


def preflight() -> None:
    require(SOURCE.is_dir() and not SOURCE.is_symlink(), "fixed_checkout_missing")
    require(run(["/usr/bin/node", "--version"], capture=True) == "v24.22.0", "node_24_22_0_required")
    require(run(["npm", "--version"], capture=True) == "11.19.1", "npm_11_19_1_required")
    require(run(["systemctl", "--version"], capture=True).splitlines()[0].startswith("systemd 255"), "systemd_255_required")
    print("Linux/tool versions checked. These checks do not validate DNS, TLS, permissions or available capacity.")


def excluded(name: str) -> bool:
    p = PurePosixPath(name)
    return (p.is_absolute() or ".." in p.parts or "__pycache__" in p.parts or p.suffix == ".pyc"
            or "node_modules" in p.parts or ".next" in p.parts or ".git" in p.parts
            or p.name.startswith(".env") or name.startswith("backend/data/")
            or p.suffix in {".sqlite", ".sqlite3", ".db", ".log", ".mp4"})


def artifact_owner() -> tuple[int, int]:
    account = pwd.getpwnam("admin")
    return account.pw_uid, account.pw_gid


def check_artifact_tree(source: Path, *, skip_cache: bool = False) -> None:
    try:
        scan_tree(source, artifact_owner(), skip_cache=skip_cache)
    except ArtifactError as error:
        raise Refused(str(error)) from None


def dependency_fingerprint() -> dict[str, object]:
    # Reuse the receipt's exact source/lock/BUILD_ID/build/static/dependency hash
    # implementation. This imports no product code and sends no API requests.
    # With node -e, argv[1] is the first extra argument. It must not be the
    # imported module's path, which would trigger that module's CLI main guard.
    script = ("import {pathToFileURL} from 'node:url';"
              "const {inspectBuild}=await import(pathToFileURL(process.argv[2]));"
              "console.log(JSON.stringify(await inspectBuild(process.argv[3])));")
    value = json.loads(run(["/usr/bin/node", "--input-type=module", "-e", script,
                           "trippilot-dependency-fingerprint", str(SOURCE / "deploy/verify-frontend.mjs"),
                           str(SOURCE / "frontend")], capture=True))
    require(isinstance(value, dict) and len(value) == 9, "artifact_fingerprint_invalid")
    return value


def dependency_permissions(apply: bool = False) -> None:
    source = SOURCE / "frontend/node_modules"  # No user-supplied path or owner.
    owner = artifact_owner()
    plan = scan_tree(source, owner, allow_writable=True)
    pending = sum(e.kind != "symlink" and bool(e.mode & 0o022) for e in plan)
    print(f"Dependency permissions checked: entries={len(plan)}, needs_changes={pending}; no files changed.")
    if not apply:
        require(pending == 0, "dependency_permissions_need_explicit_apply")
        return
    if not pending:
        print("Dependency permissions already conform; no manifest or changes required.")
        return
    commit = run(git_command("rev-parse", "HEAD"), cwd=SOURCE, capture=True)
    require(SHA.fullmatch(commit) is not None, "full_commit_required")
    manifest, changed = remove_write_bits(source, owner, plan, BASE / "receipts", dependency_fingerprint, commit=commit)
    print(f"Dependency permissions applied: changed={changed}; content fingerprints unchanged; manifest={manifest}")


def safe_copy_tree(source: Path, destination: Path, *, skip_cache: bool = False) -> None:
    check_artifact_tree(source, skip_cache=skip_cache)
    shutil.copytree(source, destination, symlinks=True, ignore=(lambda directory, names: ["cache"] if skip_cache and Path(directory) == source else []))


def archive_commit(commit: str, target: Path) -> None:
    """Reap our own producer even if extraction fails before draining its pipe."""
    with subprocess.Popen(git_command("archive", "--format=tar", commit), cwd=SOURCE, env=clean_environment(), stdout=subprocess.PIPE) as process:
        try:
            assert process.stdout is not None
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    if excluded(member.name):
                        continue
                    require(member.isfile() or member.isdir(), "tracked_links_or_special_files_refused")
                    destination = target / member.name
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True, mode=0o755)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        content = archive.extractfile(member)
                        assert content is not None
                        with destination.open("xb") as output:
                            shutil.copyfileobj(content, output)
                        destination.chmod(0o755 if member.mode & 0o111 else 0o644)
            require(process.wait() == 0, "git_archive_failed")
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            raise


def stage(commit: str, receipt: Path) -> None:
    target = checked_release(commit)
    require(not target.exists(), "release_exists_verify_or_investigate_no_overwrite")
    require(run(git_command("rev-parse", "HEAD"), cwd=SOURCE, capture=True) == commit, "checkout_commit_mismatch")
    changes = set(run(git_command("diff", "--name-only", "HEAD"), cwd=SOURCE, capture=True).splitlines())
    require(not changes - PROTECTED_CACHE, "tracked_source_dirty")
    require(receipt.is_absolute() and not receipt.is_symlink() and receipt.stat().st_size <= 65536, "bounded_absolute_receipt_required")
    proof = json.loads(receipt.read_text())
    require(proof.get("commit") == commit, "receipt_commit_mismatch")
    # The verifier rechecks build, lock, static and module bytes before copying.
    from config import check_files
    front = check_files(CONFIG)["frontend"]
    run(["/usr/bin/node", str(SOURCE / "deploy/verify-frontend.mjs"), "verify-source", "--source", str(SOURCE), "--commit", commit, "--receipt", str(receipt)], env=clean_environment() | front)
    # Check BOTH complete copied scopes before creating a release directory.
    # Copy-time rechecks below still fail closed if a source changes afterwards.
    check_artifact_tree(SOURCE / "frontend/node_modules")
    check_artifact_tree(SOURCE / "frontend/.next", skip_cache=True)
    target.mkdir(mode=0o755)  # Partial failures intentionally remain, no retry over it.
    target.chmod(0o755)
    archive_commit(commit, target)
    for directory, _, _ in os.walk(target):
        Path(directory).chmod(0o755)
    safe_copy_tree(SOURCE / "frontend/node_modules", target / "frontend/node_modules")
    safe_copy_tree(SOURCE / "frontend/.next", target / "frontend/.next", skip_cache=True)
    # Receipt is bounded nonsecret metadata; never copy .env or a source venv.
    install_new_or_identical(receipt, target / "frontend-reuse.json", 0o644)
    web = pwd.getpwnam("trippilot-web")
    ensure_directory(target / "frontend/.next/cache", web.pw_uid, web.pw_gid, 0o700)
    exclusive_json(target / "release.json", {"commit": commit, "frontend_receipt_sha256": digest(receipt), "python_venv_copied": False})
    print("Release staged at final path; backend dependencies and runtime verification are separate steps.")


def attest(commit: str, confirmed: bool) -> None:
    checked_release(commit)
    require(confirmed, "operator_build_attestation_required")
    from config import check_files
    front = check_files(CONFIG)["frontend"]
    run(["/usr/bin/node", str(SOURCE / "deploy/verify-frontend.mjs"), "attest", "--source", str(SOURCE), "--commit", commit,
         "--receipt", str(BASE / "receipts" / f"{commit}.json"), "--confirm-existing-build"], env=clean_environment() | front)


def site(tls: bool) -> None:
    available = NGINX / "sites-available"
    enabled = NGINX / "sites-enabled/trippilot"
    bootstrap, secured = available / "trippilot-http", available / "trippilot-https"
    if tls:
        require(all((CERTIFICATES / name).is_file()
                    for name in ("fullchain.pem", "privkey.pem")), "certificate_files_missing_use_http_bootstrap_first")
    target = secured if tls else bootstrap
    install_new_or_identical(Path(__file__).parent / "nginx" / ("trippilot-https.conf" if tls else "trippilot-http.conf"), target, 0o644)
    old: str | None = None
    if enabled.exists() or enabled.is_symlink():
        require(enabled.is_symlink(), "existing_site_not_managed_symlink")
        old = os.readlink(enabled)
        require(old == str(target) or tls and old == str(bootstrap), "existing_site_differs_stop")
    if old == str(target):
        run(["/usr/sbin/nginx", "-t"])
        print("Existing site verified; not reloaded.")
        return
    pending = enabled.parent / ".trippilot-pending"
    require(not pending.exists() and not pending.is_symlink(), "pending_nginx_switch_exists")
    pending.symlink_to(target)
    pending.replace(enabled)
    try:
        run(["/usr/sbin/nginx", "-t"])
    except subprocess.SubprocessError:
        # Roll back only the pointer changed by this explicit operation, never
        # overwrite a config/data file or reload a configuration that failed.
        if old is None: enabled.unlink()
        else:
            pending.symlink_to(old)
            pending.replace(enabled)
        raise
    print("Nginx site installed and syntax checked; reload is a separate operator step.")


def runtime(uv_source: Path) -> None:
    require(uv_source.is_absolute() and uv_source.is_file(), "explicit_uv_binary_required")
    require(run([str(uv_source), "--version"], capture=True).split()[:2] == ["uv", "0.13.1"], "uv_0_13_1_required")
    install_new_or_identical(uv_source, UV, 0o755)
    destination = BASE / "python"
    require(not destination.is_symlink(), "runtime_directory_symlink")
    if destination.exists():
        require(destination.is_dir() and destination.stat().st_uid == 0 and stat.S_IMODE(destination.stat().st_mode) == 0o755, "existing_runtime_permissions_differ")
    if not destination.exists():
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
        run([str(UV), "python", "install", "cpython-3.13.16-linux-x86_64-gnu"], env=clean_environment())
    interpreter = destination / "cpython-3.13.16-linux-x86_64-gnu/bin/python3.13"
    require(interpreter.is_file() and interpreter.resolve().is_relative_to(destination), "runtime_incomplete_no_auto_clear")
    require(run([str(interpreter), "-B", "-c", "import platform; print(platform.python_version())"], capture=True) == "3.13.16", "runtime_version_mismatch")
    print("Python 3.13.16 is installed at its final /opt path; no home permissions or old venv changed.")


def backend_dependencies(commit: str) -> None:
    target = checked_release(commit)
    require((target / "release.json").is_file(), "complete_stage_required")
    interpreter = BASE / "python/cpython-3.13.16-linux-x86_64-gnu/bin/python3.13"
    require(interpreter.is_file() and not interpreter.resolve().is_relative_to(Path("/home")), "final_python_runtime_required")
    ready = target / "backend-ready.json"
    lock_hash = digest(target / "backend/uv.lock")
    if ready.exists():
        require(json.loads(ready.read_text()) == {"uv_lock_sha256": lock_hash}, "backend_receipt_changed")
    else:
        require(not (target / "backend/.venv").exists(), "partial_venv_stop_no_auto_repair")
        env = clean_environment() | {"UV_PROJECT_ENVIRONMENT": str(target / "backend/.venv")}
        run([str(UV), "sync", "--frozen", "--no-dev", "--no-python-downloads", "--python", str(interpreter)], cwd=target / "backend", env=env)
    executable = target / "backend/.venv/bin/python"
    require(executable.resolve().is_relative_to(BASE / "python"), "venv_uses_wrong_runtime")
    run([str(executable), "-B", "-c", "import fastapi,sqlmodel,argon2,httpx,PIL; import sys; assert sys.prefix.endswith('/backend/.venv'); print('Backend imports OK at final venv path.')"], cwd=target / "backend", env=clean_environment())
    if not ready.exists():
        exclusive_json(ready, {"uv_lock_sha256": lock_hash})


def frontend_verification(commit: str) -> None:
    target = checked_release(commit)
    from config import check_files
    front = check_files(CONFIG)["frontend"]
    env = clean_environment() | front | {"HOME": "/nonexistent"}
    receipt = target / "frontend-reuse.json"
    for action in ("verify", "smoke"):
        argv = ["runuser", "-u", "trippilot-web", "--", "/usr/bin/node", str(target / "deploy/verify-frontend.mjs"), action,
                "--frontend", str(target / "frontend"), "--receipt", str(receipt)]
        if action == "verify": argv.append("--native")
        run(argv, env=env)
    proof = {"frontend_receipt_sha256": digest(receipt), "native_and_static_smoke": True}
    ready = target / "frontend-ready.json"
    if ready.exists(): require(json.loads(ready.read_text()) == proof, "frontend_verification_receipt_mismatch")
    else: exclusive_json(ready, proof)
    print("Final-path native modules, low-privilege startup and static hash verified; no business API requested.")


def templates() -> None:
    web = pwd.getpwnam("trippilot-web")
    for name, gid, mode in (("backend", 0, 0o600), ("frontend", web.pw_gid, 0o640)):
        install_new_or_identical(Path(__file__).parent / "env" / f"{name}.env.example", CONFIG / f"{name}.env", mode, gid)
    print("Config templates created or identical. Edit explicitly; an edited file is never overwritten on rerun.")


def units() -> None:
    require((BASE / "current/backend/.venv/bin/python").is_file(), "select_verified_release_before_units")
    for name in ("trippilot-api.service", "trippilot-web.service"):
        install_new_or_identical(Path(__file__).parent / "systemd" / name, Path("/etc/systemd/system") / name, 0o644)
    run(["systemd-analyze", "verify", "/etc/systemd/system/trippilot-api.service", "/etc/systemd/system/trippilot-web.service"])
    run(["systemctl", "daemon-reload"])
    print("Units verified and loaded; neither enabled nor started.")


def enable_video(commit: str) -> None:
    target = checked_release(commit)
    require((target / "backend-ready.json").is_file(), "final_backend_dependencies_required")
    # Explicit compatibility operation only. Missing/old fd support returns
    # failure before any access grant. Existing application limits are unchanged.
    run(["runuser", "-u", "trippilot-api", "--", str(target / "backend/.venv/bin/python"), "-B",
         str(target / "deploy/check-video.py"), "synthetic"], cwd=target / "backend", env=clean_environment() | {"HOME": "/nonexistent"})
    ready = target / "video-ready.json"
    proof = {"synthetic_production_decoder_passed": True, "commit": commit}
    if ready.exists(): require(json.loads(ready.read_text()) == proof, "video_receipt_differs")
    else: exclusive_json(ready, proof)
    directory = Path("/etc/systemd/system/trippilot-api.service.d")
    ensure_directory(directory, 0, 0, 0o755)
    install_new_or_identical(Path(__file__).parent / "systemd/video-enabled.conf.example", directory / "20-video.conf", 0o644)
    run(["systemctl", "daemon-reload"])
    print("Video compatibility passed and access drop-in prepared. Restart API explicitly to apply; no business API/model called.")


def admin(commit: str, action: str, username: str | None = None) -> None:
    target = checked_release(commit)
    from config import check_files
    check_files(CONFIG)
    argv = ["systemd-run", "--quiet", "--wait", "--collect", "--pty" if action == "invite" else "--pipe",
            "--uid=trippilot-api", "--gid=trippilot-api", f"--working-directory={target}/backend",
            "--property=EnvironmentFile=/etc/trippilot/backend.env", "--property=UMask=0077", "--property=NoNewPrivileges=yes",
            str(target / "backend/.venv/bin/python"), "-B", str(target / "deploy/data_admin.py"), action]
    if action == "invite":
        require(username is not None and re.fullmatch(r"[a-z0-9_.-]{3,48}", username) is not None, "valid_invited_username_required")
        argv.append(username)
    run(argv)


def verify_readiness(commit: str) -> Path:
    target = checked_release(commit)
    def proof(name: str) -> object:
        path = target / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536,
                "bounded_final_path_verification_receipts_required")
        return json.loads(path.read_text())
    frontend_hash = digest(target / "frontend-reuse.json")
    require(proof("release.json") == {"commit": commit, "frontend_receipt_sha256": frontend_hash,
                                      "python_venv_copied": False}, "release_receipt_mismatch")
    require(proof("backend-ready.json") == {"uv_lock_sha256": digest(target / "backend/uv.lock")},
            "backend_verification_receipt_mismatch")
    require(proof("frontend-ready.json") == {"frontend_receipt_sha256": frontend_hash,
                                             "native_and_static_smoke": True}, "frontend_verification_receipt_mismatch")
    if Path("/etc/systemd/system/trippilot-api.service.d/20-video.conf").exists():
        require(proof("video-ready.json") == {"synthetic_production_decoder_passed": True, "commit": commit},
                "video_access_requires_this_release_compatibility_check")
    return target


def select(commit: str, expected: str) -> None:
    require(expected == "none" or SHA.fullmatch(expected) is not None, "full_expected_commit_or_none_required")
    target = verify_readiness(commit)
    admin(commit, "verify")
    for unit in ("trippilot-web", "trippilot-api"):
        result = subprocess.run(["systemctl", "show", unit, "--property=LoadState", "--property=ActiveState"], text=True, stdout=subprocess.PIPE, env=clean_environment())
        status = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        require(status.get("ActiveState") == "inactive" and (result.returncode == 0 and status.get("LoadState") == "loaded"
                or result.returncode in {0, 1} and status.get("LoadState") == "not-found" and expected == "none"), "stop_both_services_before_select")
    current = BASE / "current"
    require(not current.exists() or current.is_symlink(), "current_is_not_symlink")
    old = "none"
    if current.is_symlink():
        require(current.resolve().parent == RELEASES, "current_target_outside_releases")
        old = current.resolve().name
    require(old == expected, "current_changed_since_operator_check")
    if old != commit:
        pending = BASE / "current.pending"
        require(not pending.exists() and not pending.is_symlink(), "pending_switch_exists_stop")
        pending.symlink_to(target, target_is_directory=True)
        pending.replace(current)
    print("Release selected. Persistent data unchanged; enable/start and HTTPS are separate operator steps.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("preflight", "layout", "config-templates", "install-units", "check-config", "install-http", "enable-tls"):
        sub.add_parser(action)
    command = sub.add_parser("dependency-permissions")
    command.add_argument("--apply", action="store_true", help="explicitly remove only mode 022 after full preflight and private manifest")
    command = sub.add_parser("runtime")
    command.add_argument("--uv-source", type=Path, required=True)
    for action in ("attest", "stage", "backend-deps", "verify-frontend", "enable-video", "init-identity", "init-usage", "verify-data", "invite", "select"):
        command = sub.add_parser(action)
        command.add_argument("--commit", required=True)
        if action == "stage":
            command.add_argument("--receipt", type=Path, required=True)
        if action == "attest":
            command.add_argument("--confirm-existing-build", action="store_true", required=True)
        if action == "invite":
            command.add_argument("username")
        if action == "select":
            command.add_argument("--expected-current", required=True)
    args = parser.parse_args()
    lock: int | None = None
    try:
        os.umask(0o077)
        if args.action == "preflight":
            linux()
            preflight()
        else:
            root()
            lock = deployment_lock()
            if args.action == "layout": layout()
            elif args.action == "runtime": runtime(args.uv_source)
            elif args.action == "stage": stage(args.commit, args.receipt)
            elif args.action == "attest": attest(args.commit, args.confirm_existing_build)
            elif args.action == "backend-deps": backend_dependencies(args.commit)
            elif args.action == "verify-frontend": frontend_verification(args.commit)
            elif args.action == "enable-video": enable_video(args.commit)
            elif args.action == "config-templates": templates()
            elif args.action == "dependency-permissions": dependency_permissions(args.apply)
            elif args.action == "install-units": units()
            elif args.action in {"install-http", "enable-tls"}: site(args.action == "enable-tls")
            elif args.action == "check-config":
                from config import check_files
                check_files(CONFIG)
                print("Exact production config, permissions and separated key allowlists verified.")
            elif args.action == "select": select(args.commit, args.expected_current)
            else: admin(args.commit, {"verify-data": "verify"}.get(args.action, args.action), getattr(args, "username", None))
        return 0
    except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Deployment stopped: " + (str(error) if isinstance(error, (Refused, ConfigError, ArtifactError)) else "operation_failed_inspect_locally_no_auto_retry"))
        return 1
    finally:
        if lock is not None: os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
