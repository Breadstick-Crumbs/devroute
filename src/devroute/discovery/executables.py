"""Locate executables on PATH and in project-local environments without running them."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PROJECT_ENV_DIRS = (".venv", "venv", "env")


def project_dirs(root: Path | None) -> list[Path]:
    """Project-local tool directories (virtualenvs, node_modules/.bin), searched before PATH."""
    dirs: list[Path] = []
    if root is not None:
        for name in PROJECT_ENV_DIRS:
            for sub in ("Scripts", "bin"):
                path = root / name / sub
                if path.is_dir():
                    dirs.append(path)
        node_bin = root / "node_modules" / ".bin"
        if node_bin.is_dir():
            dirs.append(node_bin)
    return dirs


def resolve_executable(name: str, root: Path | None = None) -> str | None:
    """Return the full path for ``name``, or None.

    Search order mirrors an activated project environment: project virtualenvs and
    node_modules/.bin first, then PATH, then the directory of the running interpreter.
    """
    for directory in project_dirs(root):
        found = shutil.which(name, path=str(directory))
        if found:
            return found
    found = shutil.which(name)
    if found:
        return found
    return shutil.which(name, path=str(Path(sys.executable).parent))


def describe_location(path: str, root: Path | None) -> str:
    """Human-readable location, e.g. 'on PATH' or 'in project environment .venv'."""
    if root is not None:
        try:
            rel = Path(path).resolve().relative_to(root.resolve())
            return f"in project environment {rel.parts[0]}"
        except ValueError:
            pass
    on_path = shutil.which(Path(path).name)
    if on_path and os.path.normcase(on_path) == os.path.normcase(path):
        return "on PATH"
    return f"in {Path(path).parent}"
