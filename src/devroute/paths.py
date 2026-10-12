"""Project root detection and DevRoute's on-disk locations."""

from __future__ import annotations

from pathlib import Path

STATE_DIR = ".devroute"


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor containing ``.devroute`` or ``.git``, else ``start``."""
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / STATE_DIR).is_dir() or (candidate / ".git").exists():
            return candidate
    return origin


def state_dir(root: Path) -> Path:
    return root / STATE_DIR


def project_config_path(root: Path) -> Path:
    return state_dir(root) / "config.toml"


def records_dir(root: Path) -> Path:
    return state_dir(root) / "records"


def install_manifest_path(root: Path) -> Path:
    return state_dir(root) / "install.json"


def is_initialized(root: Path) -> bool:
    return state_dir(root).is_dir()
