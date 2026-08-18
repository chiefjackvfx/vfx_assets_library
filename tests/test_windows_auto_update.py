from __future__ import annotations

from pathlib import Path
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


def test_updated_main_relaunches_with_original_arguments(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "run_vfx_asset_library.bat"
    launcher.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.delenv("SHOTBOX_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("SHOTBOX_UPDATE_RELAUNCHED", raising=False)
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

    result = updater.main([
        "--project", str(tmp_path), "--launcher", str(tmp_path / "launcher.bat"), "--", "--no-update",
    ])

    assert result == 0
    assert called == []


def test_relaunch_marker_prevents_an_update_loop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHOTBOX_UPDATE_RELAUNCHED", "1")
    called = []
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
    handoff_line = next(line for line in source.splitlines() if "windows_auto_update.py" in line and "--project" in line)
    assert handoff_line.endswith("& if errorlevel 10 exit /b 0")
