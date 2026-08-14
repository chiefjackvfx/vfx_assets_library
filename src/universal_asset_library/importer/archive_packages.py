from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Callable
from uuid import uuid4
import zipfile

from .models import ScanCancellationToken, ScanCancelled


ARCHIVE_EXTENSIONS = {".rar", ".zip"}
ARCHIVE_MEMBER_LIMIT = 4096
ARCHIVE_EXPANDED_LIMIT = 64 * 1024 * 1024 * 1024
ARCHIVE_RATIO_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ZipExtractionProgress:
    archive: str
    completed_archives: int
    total_archives: int


@dataclass(slots=True)
class ZipExtractionSummary:
    extracted: list[Path] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    canceled: bool = False


def unzip_all_zip_files(
    source: str | Path,
    *,
    progress: Callable[[ZipExtractionProgress], None] | None = None,
    cancel_token: ScanCancellationToken | None = None,
) -> ZipExtractionSummary:
    root = Path(source).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The archive source must be an existing folder.")
    token = cancel_token or ScanCancellationToken()
    archives = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.suffix.casefold() in ARCHIVE_EXTENSIONS
            and not any(part.startswith(".") for part in path.relative_to(root).parts)
        ),
        key=lambda path: str(path).casefold(),
    )
    summary = ZipExtractionSummary()
    for index, archive in enumerate(archives, start=1):
        try:
            token.check()
            target = archive.with_suffix("")
            if target.exists():
                summary.skipped[str(archive)] = (
                    f"{target.name} already exists; nothing was overwritten."
                )
                continue
            if archive.suffix.casefold() == ".zip":
                _extract_zip_atomically(archive, target, token)
            else:
                extractor = shutil.which("bsdtar")
                if not extractor:
                    raise RuntimeError(
                        "bsdtar is unavailable; install libarchive/bsdtar "
                        "to extract RAR archives."
                    )
                _extract_rar_atomically(archive, target, extractor, token)
            summary.extracted.append(target)
        except ScanCancelled:
            summary.canceled = True
            break
        except Exception as error:
            summary.failed[str(archive)] = str(error)
        finally:
            if progress:
                progress(ZipExtractionProgress(archive.name, index, len(archives)))
    return summary


def _extract_zip_atomically(
    archive: Path, target: Path, token: ScanCancellationToken
) -> None:
    stage = target.parent / f".{target.name}.shotbox-unzip-{uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if len(members) > ARCHIVE_MEMBER_LIMIT:
                raise ValueError(
                    f"The ZIP contains {len(members)} entries; "
                    f"the safety limit is {ARCHIVE_MEMBER_LIMIT}."
                )
            expanded = sum(item.file_size for item in members if not item.is_dir())
            compressed = sum(
                item.compress_size for item in members if not item.is_dir()
            )
            if (
                expanded > ARCHIVE_EXPANDED_LIMIT
                or expanded > max(compressed * ARCHIVE_RATIO_LIMIT, 1024 * 1024)
            ):
                raise ValueError("The ZIP exceeds the safe expanded-size limit.")
            for member in members:
                token.check()
                relative = _safe_member_path(member)
                destination = stage.joinpath(*relative.parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise ValueError(
                        f"The ZIP contains duplicate output paths: {member.filename}"
                    )
                with package.open(member) as source, destination.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        token.check()
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
        token.check()
        os.replace(stage, target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _extract_rar_atomically(
    archive: Path,
    target: Path,
    extractor: str,
    token: ScanCancellationToken,
) -> None:
    stage = target.parent / f".{target.name}.shotbox-unzip-{uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    process: subprocess.Popen[bytes] | None = None
    try:
        listed = subprocess.run(
            [extractor, "-tf", str(archive)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if listed.returncode:
            raise RuntimeError(
                listed.stderr.strip() or "The RAR directory could not be read."
            )
        members = [
            line.rstrip("\r")
            for line in listed.stdout.splitlines()
            if line.strip()
        ]
        if len(members) > ARCHIVE_MEMBER_LIMIT:
            raise ValueError(
                f"The RAR contains {len(members)} entries; "
                f"the safety limit is {ARCHIVE_MEMBER_LIMIT}."
            )
        verbose = subprocess.run(
            [extractor, "-tvf", str(archive)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if verbose.returncode:
            raise RuntimeError(
                verbose.stderr.strip()
                or "The RAR members could not be validated."
            )
        verbose_lines = [
            line for line in verbose.stdout.splitlines() if line.strip()
        ]
        if len(verbose_lines) != len(members):
            raise RuntimeError(
                "The RAR directory and member-type listings do not match."
            )
        member_types = [line[0] for line in verbose_lines]

        expanded = 0
        used: set[str] = set()
        archive_size = archive.stat().st_size
        for member, member_type in zip(members, member_types, strict=True):
            token.check()
            relative = _safe_archive_member_path(member, "RAR")
            if member_type == "d":
                stage.joinpath(*relative.parts).mkdir(parents=True, exist_ok=True)
                continue
            if member_type != "-":
                raise ValueError(
                    f"RAR member is not a regular file: {member}"
                )
            output_path = stage.joinpath(*relative.parts)
            output_key = str(relative).casefold()
            if output_key in used or output_path.exists():
                raise ValueError(
                    f"The RAR contains duplicate output paths: {member}"
                )
            used.add(output_key)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                [extractor, "-xOf", str(archive), "--", member],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                with output_path.open("xb") as output:
                    while True:
                        token.check()
                        chunk = (
                            process.stdout.read(1024 * 1024)
                            if process.stdout is not None
                            else b""
                        )
                        if not chunk:
                            break
                        expanded += len(chunk)
                        if (
                            expanded > ARCHIVE_EXPANDED_LIMIT
                            or expanded
                            > max(archive_size * ARCHIVE_RATIO_LIMIT, 1024 * 1024)
                        ):
                            raise ValueError(
                                "The RAR exceeds the safe expanded-size limit."
                            )
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                stderr = process.communicate(timeout=60)[1]
                if process.returncode:
                    raise RuntimeError(
                        stderr.decode("utf-8", errors="replace").strip()
                        or f"Could not extract {member}."
                    )
            except Exception:
                process.kill()
                process.communicate()
                raise
            finally:
                process = None
        token.check()
        os.replace(stage, target)
    except Exception:
        if process is not None:
            process.kill()
            process.communicate()
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _safe_member_path(member: zipfile.ZipInfo) -> Path:
    path = _safe_archive_member_path(member.filename, "ZIP")
    mode = (member.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ValueError(f"ZIP member is a symbolic link: {member.filename}")
    if member.flag_bits & 0x1:
        raise ValueError(f"Encrypted ZIP members are unsupported: {member.filename}")
    return path


def _safe_archive_member_path(member: str, archive_type: str) -> Path:
    normalized = member.replace("\\", "/")
    path = Path(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\x00" in normalized
        or "\n" in normalized
        or "\r" in normalized
    ):
        raise ValueError(f"Unsafe {archive_type} member path: {member!r}")
    return path
