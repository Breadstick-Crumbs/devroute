"""Architecture context (ADR 0011): where a project keeps its architecture rules.

Discovery finds architecture documents, decision records, architecture sections of agent
instructions, and boundary rules, read-only, and records root-relative paths (and line numbers),
never contents. STANDARD and DEEP briefs name them. Patterns and wording are data in
``architecture.toml``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from susanoo.config import ConfigError, load_toml_data
from susanoo.ui import match_paths, safe_name

if TYPE_CHECKING:
    from susanoo.discovery.models import Inventory

CATALOG = "architecture.toml"
CAPABILITY = "architecture_boundaries"
INSTRUCTIONS_READ_LIMIT = 64 * 1024
BUILD_FILE_READ_LIMIT = 2_000_000
MAX_SECTIONS_PER_FILE = 3
MAX_FOLDER_ENTRIES = 2_000  # files and folders looked at per documents folder, so a huge one stays cheap
COMPACT_ITEMS = 5


@dataclass(frozen=True)
class ArchitectureSpec:
    docs: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    doc_extensions: tuple[str, ...] = ()
    max_depth: int = 3
    max_listed: int = 5
    decisions: tuple[str, ...] = ()
    not_records: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    section_words: tuple[str, ...] = ()
    boundary_files: tuple[str, ...] = ()
    archunit_files: tuple[str, ...] = ()
    archunit_marker: str = ""
    found_note: str = ""
    compact_found_note: str = ""
    missing_note: str = ""
    compact_missing_note: str = ""


def _strings(entry: dict[str, Any], key: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{CATALOG} [architecture_context]: '{key}' must be a list of strings")
    return tuple(value)


def _positive(entry: dict[str, Any], key: str, default: int) -> int:
    value = entry.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{CATALOG} [architecture_context]: '{key}' must be a positive integer")
    return value


@lru_cache(maxsize=1)
def load_architecture_spec() -> ArchitectureSpec:
    spec = load_toml_data(CATALOG).get("architecture_context", {})
    return ArchitectureSpec(
        docs=_strings(spec, "docs"),
        folders=_strings(spec, "folders"),
        doc_extensions=tuple(e.lower() for e in _strings(spec, "doc_extensions")),
        max_depth=_positive(spec, "max_depth", 3),
        max_listed=_positive(spec, "max_listed", 5),
        decisions=_strings(spec, "decisions"),
        not_records=tuple(n.lower() for n in _strings(spec, "not_records")),
        instructions=_strings(spec, "instructions"),
        section_words=_strings(spec, "section_words"),
        boundary_files=_strings(spec, "boundary_files"),
        archunit_files=_strings(spec, "archunit_files"),
        archunit_marker=str(spec.get("archunit_marker", "")),
        found_note=str(spec.get("found_note", "")),
        compact_found_note=str(spec.get("compact_found_note", "")),
        missing_note=str(spec.get("missing_note", "")),
        compact_missing_note=str(spec.get("compact_missing_note", "")),
    )


def _read_head(path: Path, limit: int) -> str | None:
    try:
        with path.open("rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return None


_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+)$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


def _architecture_sections(path: Path, words: frozenset[str]) -> list[int]:
    """Line numbers of Markdown headings that contain one of ``words`` (fenced code skipped)."""
    text = _read_head(path, INSTRUCTIONS_READ_LIMIT)
    if text is None:
        return []
    lines: list[int] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        opened = _FENCE.match(line)
        if opened:
            if fence is None:
                fence = opened.group(1)
            elif opened.group(1) == fence:
                fence = None
            continue
        if fence is not None:
            continue
        heading = _HEADING.match(line)
        if heading and words & set(re.findall(r"[a-z]+", heading.group(1).lower())):
            lines.append(number)
            if len(lines) == MAX_SECTIONS_PER_FILE:
                break
    return lines


def _is_document(name: str, spec: ArchitectureSpec) -> bool:
    return not name.startswith(".") and name.lower().endswith(spec.doc_extensions)


def _folder_documents(root: Path, folder: str, spec: ArchitectureSpec, skip: set[str]) -> list[str]:
    """Documents under ``folder``, up to ``max_depth`` levels; overviews and shallow files first.

    Symbolic links to folders are not followed, and at most ``MAX_FOLDER_ENTRIES`` entries are
    looked at, so the count of documents not named is a lower bound in a huge folder.
    """
    found: list[str] = []
    seen = 0
    base = root / folder.rstrip("/")
    for current, dirs, files in os.walk(base):
        rel = Path(current).relative_to(root).as_posix()
        depth = len(Path(current).relative_to(base).parts)
        seen += len(dirs) + len(files)
        # Decision folders are named separately; hidden folders and deep trees are skipped.
        dirs[:] = sorted(
            d
            for d in dirs
            if safe_name(d)
            and not d.startswith(".")
            and depth + 1 < spec.max_depth
            and _key(root, f"{rel}/{d}") not in skip
        )
        found += [f"{rel}/{name}" for name in sorted(files) if safe_name(name) and _is_document(name, spec)]
        if seen >= MAX_FOLDER_ENTRIES:
            break
    first = ("readme", "index", "overview")
    found.sort(key=lambda p: (p.count("/"), Path(p).stem.lower() not in first, p.lower()))
    return found


def _count_records(root: Path, folder: str, spec: ArchitectureSpec) -> int:
    try:
        names = os.listdir(root / folder.rstrip("/"))
    except OSError:
        return 0
    return sum(
        1
        for name in names
        if _is_document(name, spec)
        and Path(name).stem.lower() not in spec.not_records
        and (root / folder / name).is_file()
    )


def _key(root: Path, rel: str) -> str:
    return os.path.normcase(os.path.realpath(root / rel.rstrip("/")))


def detect_architecture_files(root: Path) -> list[dict[str, str]]:
    """Architecture documents, decision records, sections, and boundary rules. Paths, not contents."""
    spec = load_architecture_spec()
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(entry: dict[str, str]) -> None:
        # The same file can match two patterns ("ARCHITECTURE.md" and "docs/../" spellings, or
        # case variants on Windows and macOS).
        key = _key(root, entry["path"])
        if key not in seen:
            seen.add(key)
            found.append(entry)

    decision_folders = [path for pattern in spec.decisions for path in match_paths(root, pattern)]
    skip = {_key(root, path) for path in decision_folders}
    for pattern in spec.docs:
        for path in match_paths(root, pattern):
            add({"kind": "docs", "path": path})
    for pattern in spec.folders:
        for folder in match_paths(root, pattern):
            if _key(root, folder) in skip:
                continue
            documents = _folder_documents(root, folder, spec, skip)
            for path in documents[: spec.max_listed]:
                add({"kind": "docs", "path": path})
            if len(documents) > spec.max_listed:
                found.append({"kind": "more", "path": folder, "count": str(len(documents) - spec.max_listed)})
    for folder in decision_folders:
        count = _count_records(root, folder, spec)
        if count:
            add({"kind": "decisions", "path": folder, "count": str(count)})
    words = frozenset(w.lower() for w in spec.section_words)
    for pattern in spec.instructions:
        for path in match_paths(root, pattern):
            sections = _architecture_sections(root / path, words)
            if sections:
                add({"kind": "instructions", "path": path, "lines": ", ".join(map(str, sections))})
    for pattern in spec.boundary_files:
        for path in match_paths(root, pattern):
            add({"kind": "boundaries", "path": path})
    if spec.archunit_marker:
        for pattern in spec.archunit_files:
            hit = next(
                (
                    path
                    for path in match_paths(root, pattern)
                    if spec.archunit_marker in (_read_head(root / path, BUILD_FILE_READ_LIMIT) or "")
                ),
                None,
            )
            if hit:
                add({"kind": "boundaries", "path": hit, "tool": "ArchUnit"})
                break
    return found


def architecture_label(entry: dict[str, str]) -> str:
    kind, path = entry["kind"], entry["path"]
    if kind == "more":
        return f"{entry['count']} more in {path}"
    if kind == "decisions":
        return f"decision records: {path} ({entry['count']})"
    if kind == "instructions":
        plural = "s" if "," in entry["lines"] else ""
        return f"{path} (architecture section{plural} at line{plural} {entry['lines']})"
    if kind == "boundaries" and entry.get("tool") == "ArchUnit":
        return f"ArchUnit rules in {path} (run with the test suite)"
    if kind == "boundaries":
        return f"boundary rules: {path}"
    return path


def describe_architecture(inventory: Inventory) -> list[str]:
    """Architecture context for the brief: files found and the project's boundary tools, as labels."""
    entries = inventory.architecture_files
    docs = [e for e in entries if e["kind"] == "docs"]
    others = [e for e in entries if e["kind"] not in ("docs", "more")]
    later = docs[1:] + [e for e in entries if e["kind"] == "more"]
    tools: dict[str, bool] = {}
    for finding in inventory.findings:
        if finding.kind == "tool" and CAPABILITY in finding.capabilities:
            # "dependency-cruiser (package.json depcruise script)" and "dependency-cruiser" are one tool.
            name = finding.name.partition(" (")[0]
            tools[name] = tools.get(name, False) or finding.status.usable
    checks = [
        f"boundary check: {name}" + ("" if usable else " (not runnable here)")
        for name, usable in tools.items()
    ]
    # The first document, then decisions, sections, boundary rules, and boundary tools, so the short
    # brief's first items cover every kind; further documents last.
    return (
        [architecture_label(e) for e in docs[:1] + others] + checks + [architecture_label(e) for e in later]
    )


def architecture_line(items: list[str], compact: bool = False) -> str:
    """The brief's architecture line; the short brief names at most five items, then a count."""
    spec = load_architecture_spec()
    if not items:
        return f"Architecture context: {spec.compact_missing_note if compact else spec.missing_note}"
    if compact:
        shown = "; ".join(items[:COMPACT_ITEMS])
        if len(items) > COMPACT_ITEMS:
            shown += f"; and {len(items) - COMPACT_ITEMS} more"
        return f"Architecture context: {shown}. {spec.compact_found_note}"
    return f"Architecture context: {'; '.join(items)}. {spec.found_note}"
