"""`susanoo init --global`: Susanoo as the default workflow in every project.

Installs into the user's Claude Code configuration directory (``CLAUDE_CONFIG_DIR``, default
``~/.claude``):

- the ``/susanoo`` and ``susanoo-lead`` skills and the ``susanoo-*`` subagents;
- Susanoo's hook entries in ``settings.json`` (all other settings and hooks are kept). They call
  this interpreter by absolute path with ``-P`` and are marked ``hook --global``; with ``auto``
  they include the ``UserPromptSubmit`` hook that routes coding tasks automatically;
- a playbook in ``CLAUDE.md``: a rules section written once (only when the file is new) and a
  workflow block between line-anchored markers that later installs update. Text outside the
  block is never changed, a backup is written before any change, and damaged markers or a
  symbolic link make Susanoo leave the file alone.

The install manifest is ``~/.susanoo/install.json`` (``SUSANOO_HOME``). Files are only replaced
or removed while they still match what Susanoo installed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo import __version__
from susanoo.adapters.claude_code.installer import (
    UNREADABLE,
    FileAction,
    _atomic_write,
    _read,
    lead_skill_files,
    sha256,
    skill_template,
    taste_skill_template,
)
from susanoo.adapters.claude_code.settings_hooks import apply_settings, plan_settings, settings_hooks
from susanoo.config import read_data_text
from susanoo.paths import global_manifest_path

SETTINGS = "settings.json"
PLAYBOOK = "CLAUDE.md"
_BEGIN = re.compile(r"^<!-- susanoo:begin\b[^\n]*-->[ \t]*\r?$", re.M)
_END = re.compile(r"^<!-- susanoo:end -->[ \t]*\r?$", re.M)
_GLOBAL_HOOK = re.compile(r"susanoo(?:\.exe)?\s+hook\s+--global\s")


def claude_home() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def hook_command() -> str:
    """How hooks call Susanoo: this interpreter by absolute path, valid in any folder.

    ``-P`` keeps the current folder off ``sys.path``: hooks run inside whatever project is open,
    and a project file named like a standard module (``json.py``) must never be executed.
    """
    python = Path(sys.executable).as_posix()
    quoted = f'"{python}"' if sys.platform == "win32" else shlex.quote(python)
    return f"{quoted} -P -m susanoo"


def user_command() -> str:
    """How Claude should run Susanoo: `susanoo` when it is on PATH, else the absolute form."""
    return "susanoo" if shutil.which("susanoo") else hook_command()


def file_targets(with_agents: bool, with_taste: bool = True) -> list[tuple[str, str]]:
    """(path relative to the Claude configuration directory, content) for whole-file targets."""
    items = [("skills/susanoo/SKILL.md", skill_template(False))]
    items += [(f"skills/susanoo-lead/{name}", text) for name, text in lead_skill_files()]
    if with_taste:
        items.append(("skills/taste-ui/SKILL.md", taste_skill_template()))
    if with_agents:
        for name in ("susanoo-reviewer", "susanoo-researcher"):
            items.append((f"agents/{name}.md", read_data_text("templates", "agents", f"{name}.md")))
    return items


def read_manifest() -> dict[str, Any]:
    try:
        data = json.loads(global_manifest_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}
    if not isinstance(data, dict):
        return {"files": {}}
    files = data.get("files")
    # Untrusted: only Susanoo's own targets are honored.
    known = {rel for rel, _ in file_targets(True)}
    data["files"] = (
        {k: v for k, v in files.items() if k in known and isinstance(v, str)}
        if isinstance(files, dict)
        else {}
    )
    return data


def _save_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _atomic_write(global_manifest_path(), json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _manages_settings(home: Path, manifest: dict[str, Any]) -> bool:
    """Susanoo manages the user settings if its manifest says so or its global hooks are there.

    The second case lets `--uninstall` clean up even after ``~/.susanoo`` was deleted.
    """
    if manifest.get("global") is True:
        return True
    text = _read(home / SETTINGS)
    return text not in (None, UNREADABLE) and bool(_GLOBAL_HOOK.search(str(text)))


# --- CLAUDE.md --------------------------------------------------------------------------------


def playbook_block(command: str) -> str:
    return read_data_text("templates", "playbook.md").replace("{command}", command)


def _block_span(text: str) -> tuple[int, int] | str | None:
    """The (start, end) of the one well-formed block, ``None`` if there is none, or a problem."""
    begins, ends = list(_BEGIN.finditer(text)), list(_END.finditer(text))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].end():
        return f"its Susanoo markers are damaged ({len(begins)} begin, {len(ends)} end)"
    end = ends[0].end()
    if text[end : end + 1] == "\n":
        end += 1
    return begins[0].start(), end


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def merged_playbook(existing: str | None, block: str) -> str:
    """CLAUDE.md with the workflow block added or replaced; a new file also gets the rules stub.

    Raises ``ValueError`` when the markers are damaged.
    """
    if existing is None:
        return read_data_text("templates", "playbook-rules.md").rstrip("\n") + "\n\n" + block
    nl = _newline(existing)
    block = block.replace("\n", nl) if nl != "\n" else block
    span = _block_span(existing)
    if isinstance(span, str):
        raise ValueError(span)
    if span is None:
        return existing.rstrip("\r\n") + nl + nl + block
    return existing[: span[0]] + block + existing[span[1] :]


def without_playbook(existing: str) -> str:
    span = _block_span(existing)
    if isinstance(span, str):
        raise ValueError(span)
    if span is None:
        return existing
    nl = _newline(existing)
    return (existing[: span[0]] + existing[span[1] :]).rstrip("\r\n") + nl


def _read_raw(path: Path) -> str | None:
    """Text exactly as stored (line endings and BOM kept), ``None`` if absent, or ``UNREADABLE``."""
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, OSError):
        return UNREADABLE


def _playbook_problem(path: Path) -> str | None:
    if path.is_symlink():
        return "is a symbolic link; left untouched (add the block to the file it points to yourself)"
    if path.exists() and _read_raw(path) == UNREADABLE:
        return "is not a readable text file; left untouched"
    return None


def _backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.susanoo-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


# --- install ------------------------------------------------------------------------------------


def _file_action(home: Path, rel: str, content: str, recorded: str | None, force: bool) -> FileAction:
    path = home / rel
    if path.is_symlink():
        return FileAction(rel, "conflict", "is a symbolic link; left untouched")
    current = _read(path)
    if current is None:
        return FileAction(rel, "create")
    if current == UNREADABLE:
        return FileAction(rel, "conflict", "not a readable text file; left untouched")
    if current == content:
        return FileAction(rel, "unchanged")
    if recorded == sha256(current):
        return FileAction(rel, "update", "unmodified previous version")
    if force:
        return FileAction(rel, "overwrite", "existing file will be backed up first")
    return FileAction(
        rel, "conflict", "existing file differs; left untouched (use --force to back up and replace)"
    )


def _plan_playbook(home: Path) -> FileAction:
    path = home / PLAYBOOK
    problem = _playbook_problem(path)
    if problem:
        return FileAction(PLAYBOOK, "conflict", problem)
    current = _read_raw(path)
    try:
        target = merged_playbook(None if current is None else str(current), playbook_block(user_command()))
    except ValueError as exc:
        return FileAction(PLAYBOOK, "conflict", f"{exc}; left untouched (fix or remove the markers)")
    if current is None:
        return FileAction(PLAYBOOK, "create", "rules section and workflow block")
    if target == current:
        return FileAction(PLAYBOOK, "unchanged")
    return FileAction(PLAYBOOK, "update", "workflow block written; your text kept")


def plan_global(*, auto: bool, with_agents: bool, force: bool, with_taste: bool = True) -> list[FileAction]:
    home, manifest = claude_home(), read_manifest()
    actions = [
        _file_action(home, rel, content, manifest["files"].get(rel), force)
        for rel, content in file_targets(with_agents, with_taste)
    ]
    settings = plan_settings(
        home,
        want=True,
        managed=_manages_settings(home, manifest),
        rel=SETTINGS,
        hooks=settings_hooks(hook_command(), auto=auto, global_install=True),
    )
    if settings:
        actions.append(settings)
    actions.append(_plan_playbook(home))
    return actions


def apply_global(
    actions: list[FileAction], *, auto: bool, with_agents: bool, with_taste: bool = True
) -> list[FileAction]:
    """Apply a plan from :func:`plan_global`. The manifest is saved after every step, so a partial
    failure still leaves a record of what was written."""
    home, manifest = claude_home(), read_manifest()
    content = dict(file_targets(with_agents, with_taste))
    files: dict[str, str] = dict(manifest.get("files", {}))
    manifest.update(
        {
            "global": True,
            "auto": auto,
            "command": user_command(),
            "hook_command": hook_command(),
            "files": files,
            "susanoo_version": __version__,
        }
    )
    result: list[FileAction] = []
    for action in actions:
        path = home / action.path
        if action.path == SETTINGS:
            if (
                action.action == "update"
                and not manifest.get("settings_backup")
                and (home / SETTINGS).is_file()
            ):
                manifest["settings_backup"] = _backup(home / SETTINGS).name
                action.note += f"; backup: {manifest['settings_backup']}"
            applied, created = apply_settings(
                home,
                want=True,
                managed=_manages_settings(home, manifest),
                created=manifest.get("settings_created") is True,
                rel=SETTINGS,
                hooks=settings_hooks(hook_command(), auto=auto, global_install=True),
            )
            manifest["settings_created"] = created
            result.append(applied or action)
        elif action.path == PLAYBOOK:
            if action.action in ("create", "update"):
                current = _read_raw(path)
                if current is None:
                    manifest["playbook_created"] = True
                else:
                    action.note += f"; backup: {_backup(path).name}"
                text = merged_playbook(
                    None if current is None else str(current), playbook_block(user_command())
                )
                _atomic_write(path, text)
            result.append(action)
        else:
            if action.action == "overwrite":
                action.note = f"backup: {_backup(path).name}"
            if action.action in ("create", "update", "overwrite"):
                _atomic_write(path, content[action.path])
            if action.action in ("create", "update", "overwrite", "unchanged"):
                files[action.path] = sha256(content[action.path])
            result.append(action)
        _save_manifest(manifest)
    return result


# --- uninstall ----------------------------------------------------------------------------------


def plan_global_uninstall() -> list[FileAction]:
    home, manifest = claude_home(), read_manifest()
    actions: list[FileAction] = []
    for rel, _ in file_targets(True):
        path = home / rel
        current = _read(path)
        if current is None:
            continue
        if not path.is_symlink() and current != UNREADABLE and manifest["files"].get(rel) == sha256(current):
            actions.append(FileAction(rel, "remove"))
        else:
            actions.append(FileAction(rel, "keep-modified", "modified since installation; left in place"))
    settings = plan_settings(
        home,
        want=False,
        managed=_manages_settings(home, manifest),
        created=manifest.get("settings_created") is True,
        rel=SETTINGS,
    )
    if settings:
        actions.append(settings)
    path = home / PLAYBOOK
    current = _read_raw(path)
    if current is not None:
        problem = _playbook_problem(path)
        try:
            has_block = not problem and _block_span(str(current)) is not None
            if not problem:
                without_playbook(str(current))
        except ValueError as exc:
            problem = f"{exc}; left untouched (remove the Susanoo block yourself)"
        if problem:
            actions.append(FileAction(PLAYBOOK, "conflict", problem))
        elif has_block:
            actions.append(FileAction(PLAYBOOK, "remove-block", "workflow block removed; your text kept"))
    return actions


def apply_global_uninstall(actions: list[FileAction]) -> list[FileAction]:
    home, manifest = claude_home(), read_manifest()
    result: list[FileAction] = []
    for action in actions:
        path = home / action.path
        if action.path == SETTINGS:
            applied, _ = apply_settings(
                home,
                want=False,
                managed=_manages_settings(home, manifest),
                created=manifest.get("settings_created") is True,
                rel=SETTINGS,
            )
            result.append(applied or action)
            continue
        if action.action == "remove-block":
            rest = without_playbook(str(_read_raw(path)))
            stub = read_data_text("templates", "playbook-rules.md").strip()
            if manifest.get("playbook_created") is True and rest.strip().replace("\r\n", "\n") == stub:
                path.unlink(missing_ok=True)
                action.note = "file removed: it only held what Susanoo wrote"
            else:
                action.note += f"; backup: {_backup(path).name}"
                _atomic_write(path, rest)
        elif action.action == "remove":
            path.unlink(missing_ok=True)
            parent = path.parent
            while parent != home and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        result.append(action)
    global_manifest_path().unlink(missing_ok=True)
    return result
