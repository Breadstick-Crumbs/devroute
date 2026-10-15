"""The user's taste profile for UI work, and the screenshot helpers the taste-ui skill uses (ADR 0010).

Susanoo never judges aesthetics (ADR 0008). The taste-ui skill (installed by ``susanoo init``) is how
Claude makes visual decisions: it reads a personal taste profile (``TASTE.md`` plus captured
reference sites) and takes screenshots of its own work before handing it back. This module finds
that profile and runs the two Node scripts shipped in ``data/taste`` with tools the user installed:

- ``shoot.mjs``: screenshots of a page at 375, 768 and 1280 px, plus page errors;
- ``capture.mjs``: computed design tokens and screenshots of reference sites, to build a profile.

The profile folder comes from the ``SUSANOO_TASTE_DIR`` variable, then ``[ui] taste_dir`` in the
*user* configuration, then ``~/.susanoo/taste``. Susanoo's project configuration is ignored on
purpose, and relative paths are rejected, so a repository's ``.susanoo/config.toml`` cannot point
the skill at its own files. Node and the browser are looked up on PATH while skipping the current
folder and anything inside it (on Windows only ``.exe`` files count), so a repository cannot plant
a ``node.cmd`` or ``msedge.cmd`` that runs instead of the real program.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from susanoo.paths import find_project_root, home_state, user_config_path

SCRIPTS = Path(__file__).resolve().parent / "data" / "taste"
PROFILE = "TASTE.md"
TIMEOUTS = {
    "shoot.mjs": 240,
    "capture.mjs": 3600,
}  # seconds; the scripts stop themselves sooner (180 s; 30 + 75 s per site, at most 3500 s)
SLUG = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}

_BROWSER_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "msedge",
)
_BROWSER_PATHS = {
    "win32": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ),
}


class TasteError(Exception):
    """A taste command cannot run (missing Node.js or browser, or bad arguments)."""


@dataclass(frozen=True)
class TasteLocation:
    path: Path
    source: str  # "SUSANOO_TASTE_DIR" | "user config" | "default"

    @property
    def has_profile(self) -> bool:
        return (self.path / PROFILE).is_file()

    @property
    def sites(self) -> list[str]:
        sites = self.path / "sites"
        if not sites.is_dir():
            return []
        return sorted(p.name for p in sites.iterdir() if (p / "tokens.json").is_file())

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "source": self.source,
            "profile": self.has_profile,
            "sites": self.sites,
        }


def _user_taste_dir() -> str:
    try:
        data = tomllib.loads(user_config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    ui = data.get("ui")
    value = ui.get("taste_dir", "") if isinstance(ui, dict) else ""
    return value if isinstance(value, str) else ""


def _absolute(value: str, where: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise TasteError(f"{where} must be an absolute folder path (got {value!r})")
    return path


def taste_location() -> TasteLocation:
    env = os.environ.get("SUSANOO_TASTE_DIR", "").strip()
    if env:
        return TasteLocation(_absolute(env, "SUSANOO_TASTE_DIR"), "SUSANOO_TASTE_DIR")
    configured = _user_taste_dir().strip()
    if configured:
        return TasteLocation(_absolute(configured, "[ui] taste_dir in the user configuration"), "user config")
    return TasteLocation(home_state() / "taste", "default")


def which_outside_project(name: str) -> str | None:
    """Find ``name`` on PATH, never in the current folder or the project around it, and only ``.exe`` on Windows.

    ``shutil.which`` searches the current folder first on Windows, so a repository could plant a
    ``node.cmd`` that would run instead of Node. Batch files are refused as well: cmd.exe would
    interpret characters such as ``&`` in the arguments.
    """
    cwd = Path.cwd().resolve()
    # The project around the current folder; never the home folder or a drive root, where real
    # tools live (Volta, scoop, nvm, ~/.local/bin).
    root = find_project_root(cwd)
    project: Path | None = None if root in (Path.home().resolve(), Path(root.anchor)) else root
    exts = (".exe",) if sys.platform == "win32" else ("",)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        try:
            folder = Path(entry).resolve()
        except OSError:
            continue
        if folder == cwd or (project is not None and (folder == project or project in folder.parents)):
            continue
        for ext in exts:
            candidate = folder / f"{name}{ext}"
            if candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
                return str(candidate)
    return None


def find_browser() -> str | None:
    """A Chromium-based browser for headless screenshots: SUSANOO_BROWSER, PATH, then usual install paths."""
    override = os.environ.get("SUSANOO_BROWSER", "").strip()
    if override:
        path = _absolute(override, "SUSANOO_BROWSER")
        if not path.is_file():
            raise TasteError(f"SUSANOO_BROWSER points to {override!r}, which does not exist")
        return str(path)
    for name in _BROWSER_NAMES:
        found = which_outside_project(name)
        if found:
            return found
    for candidate in _BROWSER_PATHS.get(sys.platform, ()):
        if Path(candidate).is_file():
            return candidate
    return None


def run_script(script: str, args: list[str]) -> int:
    """Run a data/taste Node script with the browser it needs; its output goes straight to the terminal."""
    browser = find_browser()
    if not browser:
        raise TasteError(
            "no Chrome, Chromium, or Edge found; set SUSANOO_BROWSER to the browser's executable"
        )
    node = which_outside_project("node")
    if not node:
        raise TasteError(
            "Node.js 22 or newer is needed for screenshots; install it, or use another browser tool"
        )
    env = {**os.environ, "SUSANOO_BROWSER": browser}
    try:
        proc = subprocess.run(  # nosec B603
            [node, str(SCRIPTS / script), *args], env=env, check=False, timeout=TIMEOUTS[script]
        )
    except subprocess.TimeoutExpired as exc:
        raise TasteError(f"{script} did not finish within {TIMEOUTS[script]} s") from exc
    return proc.returncode


def parse_sites(pairs: list[str]) -> list[str]:
    """Validate ``slug=https://...`` arguments for capture (the slug becomes a folder name)."""
    for pair in pairs:
        slug, sep, url = pair.partition("=")
        if not sep or not SLUG.fullmatch(slug) or slug.lower() in _RESERVED:
            raise TasteError(f"expected <slug>=<url> with a slug of letters, digits, - or _ (got {pair!r})")
        if not url.startswith(("https://", "http://")):
            raise TasteError(f"{slug}: the URL must start with https:// or http://")
    return pairs
