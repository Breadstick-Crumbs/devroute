"""Locate executables on PATH and in project-local environments without running them."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ENV_DIRS = (".venv", "venv", "env")

# Optional on-disk cache of PATH lookups, used by automatic routing: on Windows one lookup tests
# every PATH folder with every PATHEXT extension, which makes routing a prompt slow. The cache is
# keyed to the exact PATH and PATHEXT and expires; CLI commands never use it.
_cache: dict[str, str | None] | None = None
_cache_file: Path | None = None
_cache_meta: dict[str, object] = {}
_dirty = False


def use_path_cache(file: Path, ttl_seconds: float = 86400) -> None:
    global _cache, _cache_file, _cache_meta, _dirty
    key = hashlib.sha256(
        (os.environ.get("PATH", "") + "|" + os.environ.get("PATHEXT", "")).encode()
    ).hexdigest()
    found: dict[str, str | None] = {}
    created = time.time()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        if raw.get("key") == key and time.time() - float(raw.get("created", 0)) < ttl_seconds:
            found = {
                str(k): (str(v) if isinstance(v, str) else None) for k, v in raw.get("found", {}).items()
            }
            created = float(raw["created"])
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        pass
    _cache, _cache_file, _cache_meta, _dirty = found, file, {"key": key, "created": created}, False


def save_path_cache() -> None:
    if _cache is None or _cache_file is None or not _dirty:
        return
    try:
        _cache_file.parent.mkdir(parents=True, exist_ok=True)
        _cache_file.write_text(json.dumps({**_cache_meta, "found": _cache}), encoding="utf-8")
    except OSError:
        pass  # a cache is an optimization; never fail because of it


def _which(name: str) -> str | None:
    global _dirty
    if _cache is None:
        return shutil.which(name)
    cached = _cache.get(name)
    if cached and Path(cached).exists():
        return cached
    found = shutil.which(name)
    if found:  # misses are not cached, so a tool installed later is found immediately
        _cache[name] = found
        _dirty = True
    return found


def has_project_python_env(root: Path) -> bool:
    """True if the project has its own virtualenv (.venv, venv, env with Scripts/ or bin/)."""
    return any((root / name / sub).is_dir() for name in PROJECT_ENV_DIRS for sub in ("Scripts", "bin"))


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
    found = _which(name)
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
    on_path = _which(Path(path).name)
    if on_path and os.path.normcase(on_path) == os.path.normcase(path):
        return "on PATH"
    return f"in {Path(path).parent}"
