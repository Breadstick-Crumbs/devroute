"""Project root detection and Susanoo's on-disk locations.

Two ways a project can be active:

- **Project mode:** the project has its own ``.susanoo/`` (created by ``susanoo init``); state,
  configuration, and records live there.
- **Global mode:** ``susanoo init --global`` was run; projects need no setup. Their records live
  in ``~/.susanoo/projects/<name>-<hash>/`` so nothing is added to repositories. A
  ``.susanoo-off`` file in a project root turns Susanoo off there, and the home directory itself
  is never treated as a project.

``SUSANOO_HOME`` overrides the global state directory (default ``~/.susanoo``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

STATE_DIR = ".susanoo"
LEGACY_STATE_DIR = ".devroute"  # DevRoute, before the rename; still marks a project root
OFF_FILE = ".susanoo-off"


def home_state() -> Path:
    """Global state directory: global install manifest, user config, per-project records."""
    override = os.environ.get("SUSANOO_HOME")
    return Path(override).expanduser() if override else Path.home() / STATE_DIR


def global_manifest_path() -> Path:
    return home_state() / "install.json"


def user_config_path() -> Path:
    return home_state() / "config.toml"


def global_installed() -> bool:
    try:
        data = json.loads(global_manifest_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("global") is True


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _is_global_state(path: Path) -> bool:
    """Whether ``path`` is a global state directory rather than a project's ``.susanoo/``.

    That is the configured one, the default one in the home directory (even when
    ``SUSANOO_HOME`` points elsewhere), or any directory with global-only contents.
    """
    if _same(path, home_state()) or _same(path, Path.home() / STATE_DIR):
        return True
    if (path / "projects").is_dir() or (path / "cache").is_dir():
        return True
    try:
        return json.loads((path / "install.json").read_text(encoding="utf-8")).get("global") is True
    except (OSError, ValueError, AttributeError):
        return False


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor containing ``.susanoo``, ``.devroute``, or ``.git``, else ``start``.

    The global state directory (``~/.susanoo``) does not make the home directory a project.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        state = candidate / STATE_DIR
        if (state.is_dir() and not _is_global_state(state)) or (
            (candidate / LEGACY_STATE_DIR).is_dir() or (candidate / ".git").exists()
        ):
            return candidate
    return origin


def state_dir(root: Path) -> Path:
    """The project's own state directory (project mode)."""
    return root / STATE_DIR


def project_config_path(root: Path) -> Path:
    return state_dir(root) / "config.toml"


def install_manifest_path(root: Path) -> Path:
    return state_dir(root) / "install.json"


def project_mode(root: Path) -> bool:
    return state_dir(root).is_dir() and not _is_global_state(state_dir(root))


def global_active(root: Path) -> bool:
    """Whether the global install applies to ``root``: a git repository with no own setup.

    Folders that are not repositories (the home directory, Downloads, a drive root) never are.
    """
    if project_mode(root) or not (root / ".git").exists() or (root / OFF_FILE).exists():
        return False
    return global_installed() and not _same(root, Path.home())


def project_key(root: Path) -> str:
    """Stable folder name for a project's global records: its name plus a hash of its path."""
    resolved = str(root.resolve())
    if sys.platform == "win32":
        resolved = resolved.lower()
    name = re.sub(r"[^\w.-]+", "-", root.name).strip("-.")[:40] or "root"
    return f"{name}-{hashlib.sha256(resolved.encode('utf-8')).hexdigest()[:10]}"


def global_project_dir(root: Path) -> Path:
    return home_state() / "projects" / project_key(root)


def records_dir(root: Path) -> Path:
    if not project_mode(root) and global_active(root):
        return global_project_dir(root) / "records"
    return state_dir(root) / "records"


def is_initialized(root: Path) -> bool:
    """Whether Susanoo is active for ``root``, in project mode or global mode."""
    return project_mode(root) or global_active(root)
