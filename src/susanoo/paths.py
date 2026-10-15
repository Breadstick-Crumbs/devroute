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
    """Return the nearest ancestor containing ``.susanoo`` or ``.git``, else ``start``.

    The global state directory (``~/.susanoo``) does not make the home directory a project.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        state = candidate / STATE_DIR
        if (state.is_dir() and not _is_global_state(state)) or (candidate / ".git").exists():
            return candidate
    return origin


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, ValueError):  # ValueError: a NUL byte from a corrupt git file
        return path.absolute()


def _canonical(path: Path) -> str:
    return os.path.normcase(str(_resolved(path)))


def _read_pointer(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):  # ValueError includes UnicodeDecodeError and NUL bytes in `path`
        return None
    return text if text and "\x00" not in text else None


def _git_dir(checkout: Path) -> Path | None:
    """The git directory of a working tree: its ``.git`` folder, or where its ``.git`` file points."""
    dot = checkout / ".git"
    if dot.is_dir():
        return dot
    text = _read_pointer(dot) if dot.is_file() else None
    if text is None or not text.startswith("gitdir:"):
        return None
    target = Path(text[len("gitdir:") :].strip())
    return target if target.is_absolute() else checkout / target


def _common_dir(git_dir: Path) -> Path:
    """The repository's shared git directory (a linked worktree's ``commondir``)."""
    text = _read_pointer(git_dir / "commondir")
    if text is None:
        return git_dir
    target = Path(text)
    return target if target.is_absolute() else git_dir / target


def sibling_checkouts(root: Path) -> list[Path]:
    """The other working trees of the git repository at ``root`` (main checkout and worktrees).

    Read-only and without running git: follows ``.git`` files, ``commondir``, and
    ``<common dir>/worktrees/*/gitdir``. A folder counts only when the repository lists it (as its
    main working tree or a registered worktree) and its own ``.git`` leads back to the
    repository, so neither a stale or edited worktree entry nor a hand-written ``.git`` file in an
    unregistered folder connects an unrelated folder. ``root`` must be listed too, or there are none.
    """
    git_dir = _git_dir(root)
    if git_dir is None:
        return []
    common = _resolved(_common_dir(git_dir))  # resolved first: `.parent` of `wt/../..` is wrong
    key = _canonical(common)
    candidates = [common.parent]  # the main working tree, when the repository is not bare
    try:
        entries = sorted((common / "worktrees").iterdir())
    except OSError:
        entries = []
    for entry in entries:
        text = _read_pointer(entry / "gitdir")
        if text is not None:
            target = Path(text)
            candidates.append((target if target.is_absolute() else entry / target).parent)
    here = _canonical(root)
    if here not in {_canonical(candidate) for candidate in candidates}:
        return []
    seen = {here}
    found: list[Path] = []
    for candidate in candidates:
        name = _canonical(candidate)
        if name in seen or not candidate.is_dir():
            continue
        other = _git_dir(candidate)
        if other is None or _canonical(_common_dir(other)) != key:
            continue
        seen.add(name)
        found.append(_resolved(candidate))
    return found


_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


def head_commit(checkout: Path) -> str | None:
    """The commit checked out at ``checkout`` (its ``HEAD``), or None when it cannot be read.

    Read-only and without running git: follows ``HEAD`` to a loose ref in the worktree's or the
    common git directory, then to ``packed-refs``. Uncommitted changes are not part of it.
    """
    git_dir = _git_dir(checkout)
    head = _read_pointer(git_dir / "HEAD") if git_dir else None
    if git_dir is None or head is None:
        return None
    if not head.startswith("ref:"):
        return head if _SHA.fullmatch(head) else None
    ref = head[len("ref:") :].strip()
    parts = ref.split("/")
    if parts[0] != "refs" or "\\" in ref or ":" in ref or any(part in ("", ".", "..") for part in parts):
        return None
    common = _common_dir(git_dir)
    for base in (git_dir, common):
        value = _read_pointer(base / ref)
        if value is not None:
            return value if _SHA.fullmatch(value) else None
    packed = _read_pointer(common / "packed-refs") or ""
    for line in packed.splitlines():
        sha, _, name = line.partition(" ")
        if name == ref and _SHA.fullmatch(sha):
            return sha
    return None


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
