#!/usr/bin/env python3
"""Safely fast-forward a Windows ShotBox Assets source checkout."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse


EXPECTED_GITHUB_REPOSITORY = "chiefjackvfx/vfx_assets_library"
UPDATE_REMOTE = "origin"
UPDATE_BRANCH = "main"
UPDATE_TIMEOUT_SECONDS = 30
HANDOFF_EXIT_CODE = 10


@dataclass(frozen=True, slots=True)
class UpdateResult:
    state: str
    message: str
    before: str = ""
    after: str = ""


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


def relaunch_windows(launcher: Path, arguments: list[str], project: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("The Windows launcher can only be restarted on Windows.")
    command_processor = os.environ.get("COMSPEC", "cmd.exe")
    command = subprocess.list2cmdline(["call", str(launcher), *arguments])
    environment = os.environ.copy()
    environment["SHOTBOX_UPDATE_RELAUNCHED"] = "1"
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
    disabled = os.environ.get("SHOTBOX_AUTO_UPDATE", "").strip().casefold() in {"0", "false", "no", "off"}
    disabled = disabled or "--no-update" in values.application_arguments
    if disabled:
        print("Automatic updates are disabled; starting the installed version.")
        return 0
    if os.environ.get("SHOTBOX_UPDATE_RELAUNCHED") == "1":
        print("ShotBox Assets update applied; continuing startup.")
        return 0

    result = attempt_update(values.project)
    print(result.message)
    if result.state != "updated":
        return 0
    try:
        relaunch_windows(values.launcher.resolve(), values.application_arguments, values.project.resolve())
    except Exception as error:
        print(f"The update was installed, but ShotBox Assets could not restart automatically: {error}", file=sys.stderr)
        print("Run the Windows launcher again to start the updated version.", file=sys.stderr)
    return HANDOFF_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
