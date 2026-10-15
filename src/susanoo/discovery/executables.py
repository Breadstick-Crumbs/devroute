"""Locate executables on PATH and in project-local environments without running them."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from susanoo.discovery.project import normalize_package

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


def resolve_module_interpreter(module: str, root: Path | None = None) -> str | None:
    """The interpreter for ``python -m <module>``: the project's environment, else the one that owns
    the ``<module>`` script, else ``python`` as usual.

    Discovery finds the tool by its script; a ``python`` looked up on its own can be another
    interpreter without the tool (pipx or ``uv tool`` installs; no ``python`` on macOS or Debian).
    """
    for directory in project_dirs(root):
        found = shutil.which("python", path=str(directory))
        if found:
            return found
    script = resolve_executable(module, root)
    owner = _script_interpreter(Path(script)) if script else None
    return owner or resolve_executable("python", root)


def _script_interpreter(script: Path) -> str | None:
    """The interpreter a console script was installed for: its ``#!`` line, else a ``python`` beside
    it (a Windows ``Scripts`` folder sits next to ``python.exe``). Reads the first line only."""
    try:
        script = script.resolve()
        with script.open("rb") as handle:
            first = handle.readline(512)
    except OSError:
        return None
    if first.startswith(b"#!"):
        parts = first[2:].decode("utf-8", "replace").split()
        if len(parts) > 1 and Path(parts[0]).name == "env":
            parts = parts[1:]
        if parts and "python" in Path(parts[0]).name.lower():
            found = shutil.which(parts[0])
            if found:
                return found
    folders = [script.parent]
    if script.parent.name.lower() == "scripts":
        folders.append(script.parent.parent)
    for folder in folders:
        for name in ("python", "python3"):
            found = shutil.which(name, path=str(folder))
            if found:
                return found
    return None


def _site_packages(executable: str) -> Path | None:
    """The package folder of the environment a console script was installed into (``Scripts`` or
    ``bin`` sits beside ``Lib/site-packages`` or ``lib/python3.X/site-packages``), or ``None``
    when there is none or it is ambiguous."""
    try:
        prefix = Path(executable).resolve().parent.parent
    except OSError:
        return None
    folders = [
        prefix / "Lib" / "site-packages",
        prefix / "site-packages",
        prefix / "lib/python/site-packages",
    ]
    for pattern in ("lib/python3*/site-packages", "lib/python3*/dist-packages"):
        folders += sorted(prefix.glob(pattern))
    folders = [folder for folder in folders if folder.is_dir()]
    if len(folders) > 1:
        # Python versions sharing a prefix (~/.local, /usr/local, Homebrew): the environment is the
        # one that has the executable's own distribution, if exactly one does.
        folders = [f for f in folders if _has_distribution(f, Path(executable).stem)]
    return folders[0] if len(folders) == 1 else None


def _has_distribution(folder: Path, package: str) -> bool:
    wanted = normalize_package(package)
    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    return any(
        entry.suffix in (".dist-info", ".egg-info") and normalize_package(entry.stem.split("-")[0]) == wanted
        for entry in entries
    )


def python_package_installed(package: str, executable: str) -> bool | None:
    """Whether ``package`` is installed in the environment of ``executable``, judged by its
    ``.dist-info`` or ``.egg-info`` folder; ``None`` when that environment cannot be found (shims,
    launchers copied out of their environment, several Python versions in one prefix).
    Lists folders only; nothing is imported or run."""
    folder = _site_packages(executable)
    return None if folder is None else _has_distribution(folder, package)


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
