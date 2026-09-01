from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest

from scripts import windows_auto_update as updater
import run_vfx_asset_library as launcher


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")


def git(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )


def write_commit(checkout: Path, filename: str, content: str, message: str) -> str:
    (checkout / filename).write_text(content, encoding="utf-8")
    git(checkout, "add", filename)
    git(checkout, "commit", "-m", message)
    return git(checkout, "rev-parse", "HEAD").stdout.strip()


def repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    installed = tmp_path / "installed"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=main", str(source)], check=True, capture_output=True)
    for checkout in (source,):
        git(checkout, "config", "user.name", "ShotBox Test")
        git(checkout, "config", "user.email", "shotbox@example.invalid")
    write_commit(source, "version.txt", "one\n", "initial")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "--set-upstream", "origin", "main")
    subprocess.run(["git", "clone", "--branch", "main", str(remote), str(installed)], check=True, capture_output=True)
    git(installed, "config", "user.name", "ShotBox Test")
    git(installed, "config", "user.email", "shotbox@example.invalid")
    return remote, source, installed


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/chiefjackvfx/vfx_assets_library.git",
        "git@github.com:chiefjackvfx/vfx_assets_library.git",
        "ssh://git@github.com/chiefjackvfx/vfx_assets_library.git",
        "github.com/chiefjackvfx/vfx_assets_library",
    ],
)
def test_expected_github_origin_formats(remote_url: str) -> None:
    assert updater.is_expected_origin(remote_url)


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/example/vfx_assets_library.git",
        "https://example.com/chiefjackvfx/vfx_assets_library.git",
        "C:/local/vfx_assets_library",
    ],
)
def test_unapproved_origins_are_rejected(remote_url: str) -> None:
    assert not updater.is_expected_origin(remote_url)


def test_clean_checkout_fast_forwards_to_remote_main(tmp_path: Path) -> None:
    _remote, source, installed = repositories(tmp_path)
    target = write_commit(source, "version.txt", "two\n", "update")
    git(source, "push", "origin", "main")

    result = updater.attempt_update(installed, expected_repository=None)

    assert result.state == "updated"
    assert result.after == target
    assert git(installed, "rev-parse", "HEAD").stdout.strip() == target
    assert (installed / "version.txt").read_text(encoding="utf-8") == "two\n"


def test_dirty_checkout_is_not_fetched_or_modified(tmp_path: Path) -> None:
    _remote, source, installed = repositories(tmp_path)
    target = write_commit(source, "version.txt", "two\n", "update")
    git(source, "push", "origin", "main")
    (installed / "version.txt").write_text("local work\n", encoding="utf-8")

    result = updater.attempt_update(installed, expected_repository=None)

    assert result.state == "skipped"
    assert "local changes" in result.message
    assert git(installed, "rev-parse", "HEAD").stdout.strip() != target
    assert (installed / "version.txt").read_text(encoding="utf-8") == "local work\n"


def test_diverged_checkout_is_not_modified(tmp_path: Path) -> None:
    _remote, source, installed = repositories(tmp_path)
    remote_target = write_commit(source, "remote.txt", "remote\n", "remote update")
    git(source, "push", "origin", "main")
    local_target = write_commit(installed, "local.txt", "local\n", "local update")

    result = updater.attempt_update(installed, expected_repository=None)

    assert result.state == "skipped"
    assert "diverged" in result.message
    assert result.after == remote_target
    assert git(installed, "rev-parse", "HEAD").stdout.strip() == local_target


def test_unavailable_remote_keeps_installed_version(tmp_path: Path) -> None:
    _remote, _source, installed = repositories(tmp_path)
    before = git(installed, "rev-parse", "HEAD").stdout.strip()
    git(installed, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = updater.attempt_update(installed, expected_repository=None)

    assert result.state == "unavailable"
    assert result.before == before
    assert git(installed, "rev-parse", "HEAD").stdout.strip() == before


def test_wrong_branch_is_not_modified(tmp_path: Path) -> None:
    _remote, _source, installed = repositories(tmp_path)
    git(installed, "switch", "-c", "work")
    before = git(installed, "rev-parse", "HEAD").stdout.strip()

    result = updater.attempt_update(installed, expected_repository=None)

    assert result.state == "skipped"
    assert "expected branch main, found work" in result.message
    assert git(installed, "rev-parse", "HEAD").stdout.strip() == before


def test_unapproved_origin_is_not_fetched_or_modified(tmp_path: Path) -> None:
    _remote, _source, installed = repositories(tmp_path)
    before = git(installed, "rev-parse", "HEAD").stdout.strip()

    result = updater.attempt_update(installed)

    assert result.state == "skipped"
    assert "not the approved GitHub repository" in result.message
    assert git(installed, "rev-parse", "HEAD").stdout.strip() == before


def test_missing_git_skips_update_for_an_existing_checkout(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(updater.shutil, "which", lambda _executable: None)

    result = updater.attempt_update(tmp_path)

    assert result.state == "skipped"
    assert "Git for Windows was not found" in result.message


def test_updated_main_relaunches_with_original_arguments(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "run_vfx_asset_library.bat"
    launcher.write_text("@echo off\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    monkeypatch.delenv("SHOTBOX_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("SHOTBOX_UPDATE_RELAUNCHED", raising=False)
    monkeypatch.setattr(updater, "is_project_checkout", lambda _project: True)
    monkeypatch.setattr(
        updater,
        "attempt_update",
        lambda _project: updater.UpdateResult("updated", "Updated.", "old", "new"),
    )
    captured = {}

    def relaunch(target: Path, arguments: list[str], project: Path) -> None:
        captured.update(target=target, arguments=arguments, project=project)

    monkeypatch.setattr(updater, "relaunch_windows", relaunch)

    result = updater.main([
        "--project", str(tmp_path), "--launcher", str(launcher), "--", "--example", "value with spaces",
    ])

    assert result == updater.HANDOFF_EXIT_CODE
    assert captured["target"] == launcher.resolve()
    assert captured["arguments"] == ["--example", "value with spaces"]
    assert captured["project"] == tmp_path.resolve()


def test_no_update_argument_disables_check(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SHOTBOX_AUTO_UPDATE", raising=False)
    called = []
    monkeypatch.setattr(updater, "attempt_update", lambda _project: called.append(True))
    monkeypatch.setattr(updater, "is_project_checkout", lambda _project: True)

    result = updater.main([
        "--project", str(tmp_path), "--launcher", str(tmp_path / "launcher.bat"), "--", "--no-update",
    ])

    assert result == 0
    assert called == []


def test_relaunch_marker_prevents_an_update_loop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHOTBOX_UPDATE_RELAUNCHED", "1")
    called = []
    monkeypatch.setattr(updater, "is_project_checkout", lambda _project: True)
    monkeypatch.setattr(updater, "attempt_update", lambda _project: called.append(True))

    result = updater.main([
        "--project", str(tmp_path), "--launcher", str(tmp_path / "launcher.bat"),
    ])

    assert result == 0
    assert called == []


def test_launcher_consumes_no_update_without_dropping_application_arguments() -> None:
    assert launcher.application_arguments(["--example", "--no-update", "value with spaces"]) == [
        "--example", "value with spaces",
    ]


def test_batch_exits_on_the_same_parsed_line_after_updater_handoff() -> None:
    source = (Path(__file__).parents[1] / "run_vfx_asset_library.bat").read_text(encoding="utf-8")
    handoff_line = next(line for line in source.splitlines() if '"%UPDATE_SCRIPT%" --project' in line)
    assert handoff_line.endswith("& if errorlevel 100 exit /b 0")


def test_batch_can_install_its_own_isolated_python_runtime() -> None:
    source = (Path(__file__).parents[1] / "run_vfx_asset_library.bat").read_text(encoding="utf-8")
    assert "winget install --id 9NQ7512CXL7T" in source
    assert "https://www.python.org/ftp/python/pymanager/pymanager.appinstaller" in source
    assert 'install --target="%BOOTSTRAP_PYTHON_DIRECTORY%" 3.13' in source
    assert 'set "BOOTSTRAP_PYTHON_DIRECTORY=%SCRIPT_DIR%\\.runtime\\python"' in source
    assert 'set "BOOTSTRAP_PYTHON=%BOOTSTRAP_PYTHON_DIRECTORY%\\python.exe"' in source
    assert 'set "VENV_DIRECTORY=%SCRIPT_DIR%\\.venv"' in source


def test_standalone_batch_clones_beside_itself_and_reuses_the_checkout() -> None:
    source = (Path(__file__).parents[1] / "run_vfx_asset_library.bat").read_text(encoding="utf-8")

    assert 'set "INSTALL_TARGET=%SCRIPT_DIR%\\vfx_assets_library"' in source
    assert 'set "INSTALL_TARGET=%~f1"' in source
    assert 'if "%FIRST_ARGUMENT:~0,2%"=="--" goto collect_standalone_arguments' in source
    assert ':collect_standalone_arguments' in source
    assert 'set FORWARDED_ARGUMENTS=%FORWARDED_ARGUMENTS% "%~1"' in source
    assert 'git clone --branch main --single-branch "https://github.com/chiefjackvfx/vfx_assets_library.git" "%INSTALL_TARGET%"' in source
    assert 'if exist "%INSTALL_TARGET%\\.git" goto validate_install_target' in source
    assert 'call "%INSTALL_TARGET%\\run_vfx_asset_library.bat" %FORWARDED_ARGUMENTS%' in source


def test_batch_requires_git_only_for_install_and_rejects_unexpected_targets() -> None:
    source = (Path(__file__).parents[1] / "run_vfx_asset_library.bat").read_text(encoding="utf-8")

    assert "Git for Windows is required to install ShotBox Assets" in source
    assert "if errorlevel 1 goto launch_installed_checkout" in source
    assert "goto install_target_not_empty" in source
    assert "goto unexpected_install_origin" in source
    assert "GIT_TERMINAL_PROMPT=0" in source


def test_windows_install_has_no_legacy_archive_or_local_appdata_install_paths() -> None:
    root = Path(__file__).parents[1]
    sources = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in ("run_vfx_asset_library.bat", "scripts/windows_auto_update.py")
    )

    assert "%LOCALAPPDATA%\\ShotBoxAssets" not in sources
    assert "SHOTBOX_ARCHIVE" not in sources
    assert "SHOTBOX_INSTALL_ROOT" not in sources
    assert "SHOTBOX_VENV_ROOT" not in sources
    assert "EMBEDDED_POWERSHELL" not in sources
    assert "zipball" not in sources
    assert "state.json" not in sources


def test_batch_repairs_an_incomplete_virtual_environment_without_manual_files() -> None:
    source = (Path(__file__).parents[1] / "run_vfx_asset_library.bat").read_text(encoding="utf-8")

    assert 'if exist "%VENV_DIRECTORY%" set "VENV_NEEDS_REPAIR=1"' in source
    assert '-m venv --clear "%VENV_DIRECTORY%"' in source
    assert "Repair or remove .venv" not in source
    assert "goto venv_version_error" not in source


def test_batch_goto_targets_are_defined() -> None:
    source = (Path(__file__).parents[1] / "run_vfx_asset_library.bat").read_text(encoding="utf-8")
    labels = {
        line.strip()[1:].lower()
        for line in source.splitlines()
        if line.strip().startswith(":")
    }
    targets = set(re.findall(r"(?im)\bgoto\s+:?([a-z0-9_]+)", source))

    assert {target.lower() for target in targets} <= labels | {"eof"}
