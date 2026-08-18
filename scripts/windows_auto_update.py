#!/usr/bin/env python3
"""Safely update or download ShotBox Assets for the Windows launcher."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import zipfile


EXPECTED_GITHUB_REPOSITORY = "chiefjackvfx/vfx_assets_library"
UPDATE_REMOTE = "origin"
UPDATE_BRANCH = "main"
UPDATE_TIMEOUT_SECONDS = 30
HANDOFF_EXIT_CODE = 100
BOOTSTRAP_FAILURE_EXIT_CODE = 20
ARCHIVE_CHECK_INTERVAL_SECONDS = 15 * 60
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
GITHUB_API_ROOT = f"https://api.github.com/repos/{EXPECTED_GITHUB_REPOSITORY}"
REQUIRED_PROJECT_PATHS = (
    "pyproject.toml",
    "run_vfx_asset_library.bat",
    "run_vfx_asset_library.py",
    "scripts/windows_auto_update.py",
    "src",
)


@dataclass(frozen=True, slots=True)
class UpdateResult:
    state: str
    message: str
    before: str = ""
    after: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    state: str
    message: str
    project: Path | None = None
    commit: str = ""


def is_project_checkout(project: Path) -> bool:
    return all((project / relative).exists() for relative in REQUIRED_PROJECT_PATHS)


def archive_install_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "ShotBoxAssets" / "application"


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ShotBox-Assets-Windows-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("SHOTBOX_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_commit(branch: str = UPDATE_BRANCH) -> str:
    request = Request(f"{GITHUB_API_ROOT}/commits/{branch}", headers=_github_headers())
    with urlopen(request, timeout=UPDATE_TIMEOUT_SECONDS) as response:
        document = json.loads(response.read().decode("utf-8"))
    commit = str(document.get("sha", "")).strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("GitHub returned an invalid commit identifier.")
    return commit


def download_github_archive(commit: str, destination: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Refusing to download an invalid GitHub commit identifier.")
    request = Request(f"{GITHUB_API_ROOT}/zipball/{commit}", headers=_github_headers())
    with urlopen(request, timeout=UPDATE_TIMEOUT_SECONDS) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _load_archive_state(state_file: Path) -> dict[str, object]:
    try:
        document = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _state_project(install_root: Path, state: dict[str, object]) -> Path | None:
    commit = str(state.get("commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return None
    project = install_root / "versions" / commit
    return project if is_project_checkout(project) else None


def _write_archive_state(state_file: Path, commit: str, checked_at: float) -> None:
    document = {
        "commit": commit,
        "checked_at": checked_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_name(f".{state_file.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, state_file)


def _cache_bootstrap(install_root: Path, source: Path) -> None:
    if not source.is_file():
        return
    destination = install_root / "bootstrap" / "windows_auto_update.py"
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _safe_extract_archive(archive: Path, staging: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if not members or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("The GitHub archive is empty or exceeds the extraction limit.")
        roots: set[str] = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise RuntimeError("The GitHub archive contains an unsafe path.")
            roots.add(path.parts[0])
            file_mode = (member.external_attr >> 16) & 0o170000
            if file_mode == stat.S_IFLNK:
                raise RuntimeError("The GitHub archive contains an unsupported symbolic link.")
        if len(roots) != 1:
            raise RuntimeError("The GitHub archive does not contain one project root.")
        package.extractall(staging)
    project = staging / roots.pop()
    if not is_project_checkout(project):
        raise RuntimeError("The downloaded GitHub archive is missing required ShotBox Assets files.")
    return project


def ensure_archive_install(
    local_project: Path,
    *,
    install_root: Path | None = None,
    resolve_commit: Callable[[], str] = github_commit,
    download_archive: Callable[[str, Path], None] = download_github_archive,
    now: Callable[[], float] = time.time,
) -> ArchiveResult:
    root = (install_root or archive_install_root()).resolve()
    try:
        _cache_bootstrap(root, Path(__file__).resolve())
    except OSError:
        pass
    state_file = root / "state.json"
    state = _load_archive_state(state_file)
    cached_project = _state_project(root, state)
    if cached_project is not None:
        try:
            _cache_bootstrap(root, cached_project / "scripts" / "windows_auto_update.py")
        except OSError:
            pass
    try:
        checked_at = float(state.get("checked_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        checked_at = 0.0
    current_time = now()
    if cached_project is not None and current_time - checked_at < ARCHIVE_CHECK_INTERVAL_SECONDS:
        commit = str(state["commit"])
        return ArchiveResult("current", f"Using cached ShotBox Assets {commit[:7]}.", cached_project, commit)

    try:
        commit = resolve_commit()
    except (OSError, RuntimeError, ValueError, HTTPError, URLError) as error:
        if cached_project is not None:
            commit = str(state["commit"])
            return ArchiveResult(
                "offline",
                f"GitHub is unavailable; using cached ShotBox Assets {commit[:7]}. {error}",
                cached_project,
                commit,
            )
        if is_project_checkout(local_project):
            return ArchiveResult("local", f"GitHub is unavailable; using this downloaded copy. {error}", local_project)
        return ArchiveResult("failed", f"Could not download ShotBox Assets from GitHub: {error}")

    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return ArchiveResult("failed", "GitHub returned an invalid ShotBox Assets commit identifier.")
    destination = root / "versions" / commit
    if not is_project_checkout(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        archive_handle, archive_name = tempfile.mkstemp(prefix="shotbox-download-", suffix=".zip", dir=root)
        os.close(archive_handle)
        archive_path = Path(archive_name)
        staging = Path(tempfile.mkdtemp(prefix="shotbox-stage-", dir=destination.parent))
        try:
            download_archive(commit, archive_path)
            extracted = _safe_extract_archive(archive_path, staging)
            os.replace(extracted, destination)
        except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, HTTPError, URLError) as error:
            if is_project_checkout(destination):
                _write_archive_state(state_file, commit, current_time)
                return ArchiveResult("current", f"ShotBox Assets download is up to date ({commit[:7]}).", destination, commit)
            if cached_project is not None:
                cached_commit = str(state["commit"])
                return ArchiveResult(
                    "offline",
                    f"The GitHub download failed; using cached ShotBox Assets {cached_commit[:7]}. {error}",
                    cached_project,
                    cached_commit,
                )
            if is_project_checkout(local_project):
                return ArchiveResult("local", f"The GitHub download failed; using this downloaded copy. {error}", local_project)
            return ArchiveResult("failed", f"Could not install ShotBox Assets from GitHub: {error}")
        finally:
            try:
                archive_path.unlink()
            except OSError:
                pass
            shutil.rmtree(staging, ignore_errors=True)

    try:
        _cache_bootstrap(root, destination / "scripts" / "windows_auto_update.py")
    except OSError:
        pass
    _write_archive_state(state_file, commit, current_time)
    state_label = "downloaded" if cached_project is None or cached_project != destination else "current"
    message = (
        f"Downloaded ShotBox Assets {commit[:7]} from GitHub."
        if state_label == "downloaded"
        else f"ShotBox Assets download is up to date ({commit[:7]})."
    )
    return ArchiveResult(state_label, message, destination, commit)


def _run_git(project: Path, *arguments: str, timeout: int = UPDATE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    return subprocess.run(
        ["git", "-C", str(project), *arguments],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
        env=environment,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    lines = ((result.stderr or "") + "\n" + (result.stdout or "")).strip().splitlines()
    return lines[-1].strip() if lines else "unknown Git error"


def _github_repository(remote_url: str) -> str:
    value = remote_url.strip().replace("\\", "/")
    if re.match(r"^[^/@]+@github\.com:", value, flags=re.IGNORECASE):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.scheme:
            if (parsed.hostname or "").casefold() != "github.com":
                return ""
            path = parsed.path
        elif value.casefold().startswith("github.com/"):
            path = value.split("/", 1)[1]
        else:
            return ""
    return path.strip("/").removesuffix(".git").casefold()


def is_expected_origin(remote_url: str, expected_repository: str = EXPECTED_GITHUB_REPOSITORY) -> bool:
    return _github_repository(remote_url) == expected_repository.strip("/").removesuffix(".git").casefold()


def attempt_update(
    project: Path,
    *,
    expected_repository: str | None = EXPECTED_GITHUB_REPOSITORY,
    remote: str = UPDATE_REMOTE,
    branch: str = UPDATE_BRANCH,
) -> UpdateResult:
    project = project.resolve()
    if not (project / ".git").exists():
        return UpdateResult("skipped", "Automatic update skipped: this installation is not a Git checkout.")
    if shutil.which("git") is None:
        return UpdateResult("skipped", "Automatic update skipped: Git for Windows was not found.")

    current_branch = _run_git(project, "symbolic-ref", "--quiet", "--short", "HEAD")
    if current_branch.returncode or current_branch.stdout.strip() != branch:
        selected = current_branch.stdout.strip() or "detached HEAD"
        return UpdateResult("skipped", f"Automatic update skipped: expected branch {branch}, found {selected}.")

    remote_result = _run_git(project, "remote", "get-url", remote)
    if remote_result.returncode:
        return UpdateResult("skipped", f"Automatic update skipped: Git remote {remote} is unavailable.")
    remote_url = remote_result.stdout.strip()
    if expected_repository and not is_expected_origin(remote_url, expected_repository):
        return UpdateResult("skipped", f"Automatic update skipped: {remote} is not the approved GitHub repository.")

    dirty = _run_git(project, "status", "--porcelain=v1", "--untracked-files=no")
    if dirty.returncode:
        return UpdateResult("skipped", f"Automatic update skipped: {_detail(dirty)}.")
    if dirty.stdout.strip():
        return UpdateResult("skipped", "Automatic update skipped: tracked local changes are present.")

    before_result = _run_git(project, "rev-parse", "HEAD")
    if before_result.returncode:
        return UpdateResult("skipped", f"Automatic update skipped: {_detail(before_result)}.")
    before = before_result.stdout.strip()

    try:
        fetched = _run_git(
            project,
            "fetch",
            "--quiet",
            "--prune",
            remote,
            f"refs/heads/{branch}:refs/remotes/{remote}/{branch}",
        )
    except subprocess.TimeoutExpired:
        return UpdateResult("unavailable", "Update check timed out; starting the installed version.", before=before)
    if fetched.returncode:
        return UpdateResult(
            "unavailable",
            f"Update check unavailable; starting the installed version. {_detail(fetched)}",
            before=before,
        )

    target = f"refs/remotes/{remote}/{branch}"
    target_result = _run_git(project, "rev-parse", "--verify", target)
    if target_result.returncode:
        return UpdateResult("unavailable", f"Update check did not find {remote}/{branch}; starting the installed version.", before=before)
    target_commit = target_result.stdout.strip()
    if before == target_commit:
        return UpdateResult("current", f"ShotBox Assets is up to date ({before[:7]}).", before=before, after=before)

    ancestor = _run_git(project, "merge-base", "--is-ancestor", before, target_commit)
    if ancestor.returncode == 1:
        return UpdateResult(
            "skipped",
            f"Automatic update skipped: local {branch} has diverged from {remote}/{branch}.",
            before=before,
            after=target_commit,
        )
    if ancestor.returncode:
        return UpdateResult("skipped", f"Automatic update skipped: {_detail(ancestor)}.", before=before)

    merged = _run_git(project, "merge", "--ff-only", "--quiet", target)
    if merged.returncode:
        return UpdateResult(
            "failed",
            f"Automatic update failed safely; run Git manually for details. {_detail(merged)}",
            before=before,
            after=target_commit,
        )
    return UpdateResult(
        "updated",
        f"Updated ShotBox Assets {before[:7]} -> {target_commit[:7]}.",
        before=before,
        after=target_commit,
    )


def relaunch_windows(
    launcher: Path,
    arguments: list[str],
    project: Path,
    *,
    environment_updates: dict[str, str] | None = None,
) -> None:
    if os.name != "nt":
        raise RuntimeError("The Windows launcher can only be restarted on Windows.")
    command_processor = os.environ.get("COMSPEC", "cmd.exe")
    command = subprocess.list2cmdline(["call", str(launcher), *arguments])
    environment = os.environ.copy()
    environment["SHOTBOX_UPDATE_RELAUNCHED"] = "1"
    environment.pop("SHOTBOX_GITHUB_TOKEN", None)
    environment.update(environment_updates or {})
    subprocess.Popen(
        [command_processor, "/d", "/s", "/c", command],
        cwd=str(project),
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=False,
    )


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("application_arguments", nargs=argparse.REMAINDER)
    values = parser.parse_args(argv)
    if values.application_arguments[:1] == ["--"]:
        values.application_arguments = values.application_arguments[1:]
    return values


def main(argv: list[str] | None = None) -> int:
    values = _arguments(argv)
    local_project = values.project.resolve()
    project_is_valid = is_project_checkout(local_project)
    disabled = os.environ.get("SHOTBOX_AUTO_UPDATE", "").strip().casefold() in {"0", "false", "no", "off"}
    disabled = disabled or "--no-update" in values.application_arguments
    if disabled and project_is_valid:
        print("Automatic updates are disabled; starting the installed version.")
        return 0
    if os.environ.get("SHOTBOX_UPDATE_RELAUNCHED") == "1" or os.environ.get("SHOTBOX_ARCHIVE_INSTALL") == "1":
        print("ShotBox Assets update applied; continuing startup.")
        return 0

    if (local_project / ".git").exists():
        result = attempt_update(local_project)
        print(result.message)
        if result.state != "updated":
            return 0
        try:
            relaunch_windows(values.launcher.resolve(), values.application_arguments, local_project)
        except Exception as error:
            print(f"The update was installed, but ShotBox Assets could not restart automatically: {error}", file=sys.stderr)
            print("Run the Windows launcher again to start the updated version.", file=sys.stderr)
        return HANDOFF_EXIT_CODE

    archive = ensure_archive_install(local_project)
    print(archive.message)
    if archive.project is None:
        return BOOTSTRAP_FAILURE_EXIT_CODE
    if archive.state == "local" and archive.project == local_project:
        return 0
    install_root = archive.project.parents[1]
    environment = {
        "SHOTBOX_ARCHIVE_INSTALL": "1",
        "SHOTBOX_ARCHIVE_COMMIT": archive.commit,
        "SHOTBOX_VENV_ROOT": str(install_root / ".venv"),
    }
    try:
        relaunch_windows(
            archive.project / "run_vfx_asset_library.bat",
            values.application_arguments,
            archive.project,
            environment_updates=environment,
        )
    except Exception as error:
        print(f"ShotBox Assets was downloaded, but it could not restart automatically: {error}", file=sys.stderr)
        print("Run the Windows launcher again to start the downloaded version.", file=sys.stderr)
    return HANDOFF_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
