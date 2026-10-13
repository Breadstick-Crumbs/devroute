"""Migration of a DevRoute installation (v0.1, v0.2) to Susanoo.

DevRoute was renamed to Susanoo in v0.3. ``susanoo init`` migrates in two phases:

1. State, before installing: ``.devroute/`` moves to ``.susanoo/``. If ``.susanoo/`` already
   exists (a teammate committed the migration), the local records are merged into it instead.
   A failure here raises ``MigrationError`` before anything else changes.
2. Files, after the new skill is installed: the old ``/devroute`` skill and ``devroute-*``
   subagents are removed, but only while their content still matches what DevRoute installed
   (its manifest, or a file DevRoute shipped). Anything else is left in place and reported.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from susanoo.adapters.claude_code.installer import (
    UNREADABLE,
    FileAction,
    _atomic_write,
    _read,
    sha256,
)
from susanoo.paths import LEGACY_STATE_DIR, STATE_DIR, install_manifest_path, records_dir, state_dir

LEGACY_SKILL = ".claude/skills/devroute/SKILL.md"
LEGACY_INTEGRATION = (
    LEGACY_SKILL,
    ".claude/agents/devroute-reviewer.md",
    ".claude/agents/devroute-researcher.md",
)
LEGACY_STATE_FILES = (f"{LEGACY_STATE_DIR}/config.toml", f"{LEGACY_STATE_DIR}/.gitignore")

# sha256 of every file DevRoute v0.1.0 and v0.2.0 could install (both skill variants), so
# copies committed to a repository are recognized even without a local install manifest.
SHIPPED: dict[str, frozenset[str]] = {
    ".claude/agents/devroute-researcher.md": frozenset(
        {
            "5c661211ab380ec0269501268e9819d8e2eae6a9a07fcf99589c4a83043ebfe0",
            "b3ef155ce2be7049e067c9b4d71f10f61cab3643d829fb30ec97d474cf271914",
        }
    ),
    ".claude/agents/devroute-reviewer.md": frozenset(
        {
            "0f98984cacd920bdc5604dd3e21a586bbe03762ede203769677e9a7f932c2e2a",
        }
    ),
    ".claude/skills/devroute/SKILL.md": frozenset(
        {
            "00722990392d535668307d7644333ff817138daba8c9a375cdde79236e1a9c0f",
            "0e22abc3fa8d902f73292b638c2c59670b56ae260017b165aea8b1475bf59161",
            "c054b3030cf50917c801f9678471a741f9b15be1b5012e6a69e0dec5d3afacdc",
            "db0c2d5d4b70a119c33aa228c650f8ab19e95869298133d6631adfdba15a75f1",
        }
    ),
    ".devroute/.gitignore": frozenset(
        {
            "bb2eebf1b0a22886d2ded85e98ebf1161086fd9f881b7f15d2d80d3233c61543",
        }
    ),
    ".devroute/config.toml": frozenset(
        {
            "0b263276e51c9c07cafff8b2387958ffdf09efc3df417ad371d3d60cc2887b8d",
        }
    ),
}


class MigrationError(Exception):
    """The DevRoute state could not be migrated; nothing else was changed."""


def legacy_present(root: Path) -> bool:
    """Whether a DevRoute installation (state directory or integration files) is present."""
    return (root / LEGACY_STATE_DIR).is_dir() or any((root / p).is_file() for p in LEGACY_INTEGRATION)


def legacy_manifest(root: Path) -> dict[str, Any]:
    path = root / LEGACY_STATE_DIR / "install.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return {"files": {}}
    # Untrusted, like the current manifest: only DevRoute's own paths are honored.
    data["files"] = {k: v for k, v in data["files"].items() if k in SHIPPED and isinstance(v, str)}
    return data


def _devroute_owned(rel: str, content: str, files: dict[str, str]) -> bool:
    digest = sha256(content)
    return files.get(rel) == digest or digest in SHIPPED.get(rel, frozenset())


def migrated_manifest(root: Path) -> dict[str, Any]:
    """The Susanoo manifest for migrated state: carried-over state-file hashes and enforce choice."""
    legacy = legacy_manifest(root)
    files = {k: v for k, v in legacy["files"].items() if k in LEGACY_STATE_FILES}
    for rel in LEGACY_STATE_FILES:
        # Without a manifest entry (a teammate's clone), a file exactly as DevRoute shipped it
        # still counts as unmodified, so `init` can update it.
        current = _read(root / rel)
        if rel not in files and current not in (None, UNREADABLE) and sha256(str(current)) in SHIPPED[rel]:
            files[rel] = sha256(str(current))
    prefix = f"{LEGACY_STATE_DIR}/"
    return {
        "files": {f"{STATE_DIR}/{k[len(prefix) :]}": v for k, v in files.items()},
        "enforce": legacy.get("enforce") is True,
        "migrated_from": {"devroute_version": str(legacy.get("devroute_version", "unknown"))},
    }


def state_sources(root: Path) -> dict[str, Path]:
    """Where a move would take ``.susanoo/`` state files from (to preview a dry run)."""
    return {f"{STATE_DIR}/{Path(p).name}": root / p for p in LEGACY_STATE_FILES}


def plan_migration(root: Path) -> list[FileAction]:
    actions: list[FileAction] = []
    if (root / LEGACY_STATE_DIR).is_dir():
        if state_dir(root).exists():
            note = f"records merged into {STATE_DIR}/records; the rest is removed if DevRoute created it"
            actions.append(FileAction(f"{LEGACY_STATE_DIR}/", "merge", note))
        else:
            note = f"moved to {STATE_DIR}/ with records and configuration"
            actions.append(FileAction(f"{LEGACY_STATE_DIR}/", "migrate", note))
    return actions + plan_legacy_files(root)


def plan_legacy_files(root: Path) -> list[FileAction]:
    """Removal plan for the old /devroute skill and devroute-* subagents."""
    files = legacy_manifest(root)["files"]
    actions: list[FileAction] = []
    for rel in LEGACY_INTEGRATION:
        current = _read(root / rel)
        if current is None:
            continue
        if current == UNREADABLE:
            actions.append(FileAction(rel, "keep-modified", "not a readable text file; left in place"))
        elif _devroute_owned(rel, current, files):
            actions.append(FileAction(rel, "remove", "DevRoute file, replaced by Susanoo's"))
        else:
            note = "not a file DevRoute installed, or changed since; remove it yourself"
            actions.append(FileAction(rel, "keep-modified", note))
    return actions


def planned_enforce(root: Path, actions: list[FileAction], current: bool) -> bool:
    """The enforce choice after migration: DevRoute's, unless Susanoo already recorded one."""
    takes_legacy = any(a.action == "migrate" for a in actions) or (
        any(a.action == "merge" for a in actions) and not install_manifest_path(root).is_file()
    )
    return bool(legacy_manifest(root).get("enforce") is True) if takes_legacy else current


def migrate_state(root: Path, actions: list[FileAction]) -> str | None:
    """Phase 1: move or merge ``.devroute/``. Raises ``MigrationError`` if it cannot.

    Returns a note when a merge leaves files in ``.devroute/`` that DevRoute did not create.
    """
    legacy = root / LEGACY_STATE_DIR
    try:
        if any(a.action == "migrate" for a in actions):
            manifest = migrated_manifest(root)
            os.replace(legacy, state_dir(root))
            _atomic_write(install_manifest_path(root), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        elif any(a.action == "merge" for a in actions):
            _merge_state(root)
            if legacy.exists():
                return f"{LEGACY_STATE_DIR}/ still has files Susanoo did not create; review and delete it yourself"
    except OSError as exc:
        raise MigrationError(
            f"could not migrate {LEGACY_STATE_DIR}/ to {STATE_DIR}/ ({exc}). Is a file in it open? "
            "Nothing else was changed; close it and run `susanoo init` again."
        ) from exc
    return None


def _merge_state(root: Path) -> None:
    legacy = root / LEGACY_STATE_DIR
    if not install_manifest_path(root).is_file():
        manifest = migrated_manifest(root)
        _atomic_write(install_manifest_path(root), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    old_records = legacy / "records"
    if old_records.is_dir():
        new_records = records_dir(root)
        # Record files are append-only JSONL; each is removed once appended, so a rerun after a
        # failure never appends it twice.
        for src in sorted(old_records.glob("*.jsonl")):
            dst = new_records / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            if text and not text.endswith("\n"):
                text += "\n"
            with dst.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            src.unlink()
        hooks = old_records / "hooks"
        if hooks.is_dir():
            for src in sorted(hooks.glob("*.json")):
                dst = new_records / "hooks" / src.name
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                src.unlink()
            _prune(hooks, root)
        _prune(old_records, root)
    files = legacy_manifest(root)["files"]
    for rel in LEGACY_STATE_FILES:
        current = _read(root / rel)
        if current not in (None, UNREADABLE) and _devroute_owned(rel, str(current), files):
            (root / rel).unlink()
    (legacy / "install.json").unlink(missing_ok=True)
    _prune(legacy, root)


def remove_legacy_files(root: Path, actions: list[FileAction]) -> None:
    """Phase 2: delete the old integration files planned for removal."""
    for action in actions:
        if action.action == "remove":
            path = root / action.path
            path.unlink(missing_ok=True)
            _prune(path.parent, root)


def _prune(directory: Path, root: Path) -> None:
    """Remove ``directory`` and its parents while they are empty (never ``root``)."""
    while directory != root and directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
        directory = directory.parent


def stale_hook_settings(root: Path, home: Path) -> list[Path]:
    """Settings files that still run `devroute hook` (the documented compaction snippet)."""
    candidates = [
        root / ".claude" / "settings.json",
        root / ".claude" / "settings.local.json",
        home / ".claude" / "settings.json",
    ]
    stale = []
    for path in candidates:
        text = _read(path)
        if text not in (None, UNREADABLE) and "devroute hook" in str(text):
            stale.append(path)
    return stale
