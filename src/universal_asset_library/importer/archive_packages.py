from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Callable
from uuid import uuid4
import zipfile

from .models import ScanCancellationToken, ScanCancelled


ZIP_MEMBER_LIMIT = 4096
ZIP_EXPANDED_LIMIT = 64 * 1024 * 1024 * 1024
ZIP_RATIO_LIMIT = 500


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
        raise ValueError("The ZIP source must be an existing folder.")
    token = cancel_token or ScanCancellationToken()
    archives = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.suffix.casefold() == ".zip"
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
            _extract_zip_atomically(archive, target, token)
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
            if len(members) > ZIP_MEMBER_LIMIT:
                raise ValueError(
                    f"The ZIP contains {len(members)} entries; "
                    f"the safety limit is {ZIP_MEMBER_LIMIT}."
                )
            expanded = sum(item.file_size for item in members if not item.is_dir())
            compressed = sum(
                item.compress_size for item in members if not item.is_dir()
            )
            if (
                expanded > ZIP_EXPANDED_LIMIT
                or expanded > max(compressed * ZIP_RATIO_LIMIT, 1024 * 1024)
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


def _safe_member_path(member: zipfile.ZipInfo) -> Path:
    normalized = member.filename.replace("\\", "/")
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
        raise ValueError(f"Unsafe ZIP member path: {member.filename!r}")
    mode = (member.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ValueError(f"ZIP member is a symbolic link: {member.filename}")
    if member.flag_bits & 0x1:
        raise ValueError(f"Encrypted ZIP members are unsupported: {member.filename}")
    return path
