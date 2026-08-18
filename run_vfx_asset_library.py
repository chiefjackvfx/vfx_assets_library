#!/usr/bin/env python3
"""Launch the ShotBox VFX asset library from this source checkout."""

from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
LAUNCHER_ONLY_ARGUMENTS = {"--no-update"}


def application_arguments(arguments: list[str]) -> list[str]:
    """Remove flags consumed by the source-checkout launcher."""
    return [value for value in arguments if value not in LAUNCHER_ONLY_ARGUMENTS]


def _restart_in_project_venv() -> None:
    """Use the project's virtual environment when one is available."""
    candidates = (
        PROJECT_ROOT / ".venv" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
    )

    for interpreter in candidates:
        if interpreter.is_file() and Path(sys.executable).resolve() != interpreter.resolve():
            os.execv(
                str(interpreter),
                [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]],
            )


def main() -> int:
    sys.argv[:] = [sys.argv[0], *application_arguments(sys.argv[1:])]
    _restart_in_project_venv()

    source_directory = PROJECT_ROOT / "src"
    sys.path.insert(0, str(source_directory))

    from universal_asset_library.app import main as run_application

    return run_application()


if __name__ == "__main__":
    raise SystemExit(main())
