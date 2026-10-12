"""Read-only scanning of project files: languages, manifests, and declared dependencies.

Nothing here executes code. Files are read with size limits and parse errors are
reported as warnings rather than raised.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cxx": "cpp",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".vue": "vue",
    ".svelte": "svelte",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
}

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "vendor",
        ".next",
        ".nuxt",
        ".idea",
        ".vscode",
        "coverage",
        ".gradle",
        ".terraform",
        "site-packages",
        ".devroute",
        ".claude",
    }
)

BUILD_FILES: dict[str, str] = {
    "pyproject.toml": "Python (pyproject)",
    "setup.py": "Python (setuptools)",
    "setup.cfg": "Python (setuptools)",
    "package.json": "Node.js (npm)",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "Yarn",
    "bun.lockb": "Bun",
    "Cargo.toml": "Cargo",
    "go.mod": "Go modules",
    "pom.xml": "Maven",
    "build.gradle": "Gradle",
    "build.gradle.kts": "Gradle",
    "Gemfile": "Bundler",
    "composer.json": "Composer",
    "CMakeLists.txt": "CMake",
    "Makefile": "Make",
    "Dockerfile": "Docker",
    "uv.lock": "uv",
    "poetry.lock": "Poetry",
    "Pipfile": "Pipenv",
}

MAX_FILES = 50_000
MAX_DEPTH = 10
MAX_READ_BYTES = 2_000_000

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", re.MULTILINE)


@dataclass
class ProjectFacts:
    root: Path
    languages: dict[str, int] = field(default_factory=dict)
    source_files: int = 0
    truncated: bool = False
    build_files: list[str] = field(default_factory=list)
    python_packages: set[str] = field(default_factory=set)
    npm_packages: set[str] = field(default_factory=set)
    npm_scripts: dict[str, str] = field(default_factory=dict)
    make_targets: set[str] = field(default_factory=set)
    pyproject_tools: set[str] = field(default_factory=set)
    is_git_repo: bool = False
    git_remotes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def has_file(self, pattern: str) -> bool:
        """True if a root-relative path or glob exists (globs are not recursive)."""
        if any(ch in pattern for ch in "*?["):
            return any(True for _ in self.root.glob(pattern))
        return (self.root / pattern).exists()

    def npm_installed(self, package: str) -> bool:
        return (self.root / "node_modules" / package).is_dir()


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_text(path: Path, facts: ProjectFacts) -> str | None:
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            facts.warnings.append(f"{path.name}: skipped (larger than {MAX_READ_BYTES} bytes)")
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        facts.warnings.append(f"{path.name}: cannot read ({exc.strerror or exc})")
        return None


def _requirement_name(spec: str) -> str | None:
    match = _REQ_NAME.match(spec)
    return normalize_package(match.group(1)) if match else None


def _scan_languages(facts: ProjectFacts) -> None:
    counted = 0
    root_depth = len(facts.root.parts)
    for dirpath, dirnames, filenames in os.walk(facts.root):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        if len(current.parts) - root_depth >= MAX_DEPTH:
            dirnames[:] = []
        for name in filenames:
            counted += 1
            if counted > MAX_FILES:
                facts.truncated = True
                facts.warnings.append(
                    f"file scan stopped after {MAX_FILES} files; language counts are partial"
                )
                return
            language = EXTENSION_LANGUAGES.get(os.path.splitext(name)[1].lower())
            if language:
                facts.languages[language] = facts.languages.get(language, 0) + 1
                facts.source_files += 1


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_pyproject(facts: ProjectFacts) -> None:
    path = facts.root / "pyproject.toml"
    text = _read_text(path, facts) if path.is_file() else None
    if text is None:
        return
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        facts.warnings.append(f"pyproject.toml: invalid TOML ({exc})")
        return
    # Every level is type-checked: a malformed manifest must degrade, not crash.
    specs: list[Any] = []
    project = _table(data.get("project"))
    specs += _array(project.get("dependencies"))
    for group in _table(project.get("optional-dependencies")).values():
        specs += _array(group)
    for group in _table(data.get("dependency-groups")).values():
        specs += _array(group)
    tool = _table(data.get("tool"))
    poetry = _table(tool.get("poetry"))
    names = list(_table(poetry.get("dependencies"))) + list(_table(poetry.get("dev-dependencies")))
    for group in _table(poetry.get("group")).values():
        names += list(_table(_table(group).get("dependencies")))
    for spec in specs:
        if isinstance(spec, str) and (name := _requirement_name(spec)):
            facts.python_packages.add(name)
    facts.python_packages.update(normalize_package(n) for n in names if n.lower() != "python")
    facts.pyproject_tools.update(tool.keys())


def _parse_requirements(facts: ProjectFacts) -> None:
    candidates = list(facts.root.glob("requirements*.txt")) + list(facts.root.glob("requirements/*.txt"))
    for path in candidates:
        text = _read_text(path, facts)
        if text is None:
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            if name := _requirement_name(line):
                facts.python_packages.add(name)


def _parse_package_json(facts: ProjectFacts) -> None:
    path = facts.root / "package.json"
    text = _read_text(path, facts) if path.is_file() else None
    if text is None:
        return
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        facts.warnings.append(f"package.json: invalid JSON ({exc.msg})")
        return
    if not isinstance(data, dict):
        return
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            facts.npm_packages.update(str(k) for k in section)
    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        facts.npm_scripts = {k: str(v) for k, v in scripts.items()}
    if (
        "typescript" in facts.npm_packages
        and "typescript" not in facts.languages
        and facts.has_file("tsconfig.json")
    ):
        facts.languages.setdefault("typescript", 0)


def _parse_makefile(facts: ProjectFacts) -> None:
    path = facts.root / "Makefile"
    text = _read_text(path, facts) if path.is_file() else None
    if text:
        facts.make_targets.update(
            m.group(1) for m in _MAKE_TARGET.finditer(text) if not m.group(1).startswith(".")
        )


def _parse_git(facts: ProjectFacts) -> None:
    # The project may be a subdirectory of a repository, so look upward for .git.
    git = next(
        (d / ".git" for d in (facts.root, *facts.root.resolve().parents) if (d / ".git").exists()), None
    )
    facts.is_git_repo = git is not None
    if git is None:
        return
    config = git / "config"
    if not config.is_file():
        return
    text = _read_text(config, facts) or ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("url = "):
            facts.git_remotes.append(_redact_url(line[len("url = ") :]))


_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@")


def _redact_url(url: str) -> str:
    """Strip user info (which may hold tokens or passwords) from any scheme://user@host URL."""
    return _URL_USERINFO.sub(r"\1", url)


def scan_project(root: Path) -> ProjectFacts:
    facts = ProjectFacts(root=root)
    _scan_languages(facts)
    facts.build_files = [name for name in BUILD_FILES if (root / name).is_file()]
    _parse_pyproject(facts)
    _parse_requirements(facts)
    _parse_package_json(facts)
    _parse_makefile(facts)
    _parse_git(facts)
    return facts
