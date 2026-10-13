"""Project root detection and Susanoo's on-disk locations."""

from __future__ import annotations

from pathlib import Path

STATE_DIR = ".susanoo"
LEGACY_STATE_DIR = ".devroute"  # DevRoute, before the rename; still marks a project root


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor containing ``.susanoo``, ``.devroute``, or ``.git``, else ``start``."""
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (
            any((candidate / d).is_dir() for d in (STATE_DIR, LEGACY_STATE_DIR))
            or (candidate / ".git").exists()
        ):
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
