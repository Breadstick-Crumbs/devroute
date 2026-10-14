"""UI domains and checklists (ADR 0008): which kind of product a UI task is for, and what to check.

The domain is a heuristic: `[ui] domain` in configuration, then phrases and then words in the task,
then project detection (dependencies and files, read during discovery). Briefs always say which
source chose it.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from susanoo.config import Config, ConfigError, load_toml_data, read_data_text

if TYPE_CHECKING:
    from susanoo.discovery.models import Inventory

NO_DOMAIN = "none"  # `[ui] domain = "none"`: generic baseline only, no detection
CATALOG = "ui_domains.toml"
CSS_READ_LIMIT = 64 * 1024  # bytes


@dataclass(frozen=True)
class Checklist:
    title: str
    checklist: tuple[str, ...]
    focus: str
    sources: str


@dataclass(frozen=True)
class UIDomain(Checklist):
    id: str = ""
    task_phrases: tuple[str, ...] = ()
    task_words: tuple[str, ...] = ()
    npm_packages: tuple[str, ...] = ()
    unless_npm_packages: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    fallback: bool = False


@dataclass(frozen=True)
class DesignSpec:
    """Where design context lives and what the brief says about it (`[design_context]`)."""

    docs: tuple[str, ...] = ()
    tokens: tuple[str, ...] = ()
    css_files: tuple[str, ...] = ()
    min_custom_properties: int = 5
    storybook: tuple[str, ...] = ()
    figma: tuple[str, ...] = ()
    taste_skill: str = ""
    taste_note: str = ""
    found_note: str = ""
    missing_note: str = ""
    compact_found_note: str = ""
    compact_missing_note: str = ""


@dataclass(frozen=True)
class UICatalog:
    baseline: Checklist
    domains: tuple[UIDomain, ...]
    design: DesignSpec = field(default_factory=DesignSpec)

    def get(self, domain_id: str) -> UIDomain | None:
        return next((d for d in self.domains if d.id == domain_id), None)


@dataclass
class UIContext:
    """The UI part of a plan: the chosen domain (if any), why, and what to check."""

    domain: str | None
    title: str | None
    reason: str
    baseline: Checklist
    checklist: Checklist | None = None
    # config | task | project | none: only task words and project detection are guesses.
    source: str = "none"
    detected: list[dict[str, str]] = field(default_factory=list)
    # Design context found in the project ("DESIGN.md", "tokens: tailwind.config.ts", ...).
    design: list[str] = field(default_factory=list)
    # The taste skill's note, when that skill is installed.
    taste_note: str = ""

    @property
    def heuristic(self) -> bool:
        return self.source in ("task", "project")

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "title": self.title,
            "reason": self.reason,
            "source": self.source,
            "baseline": list(self.baseline.checklist),
            "checklist": list(self.checklist.checklist) if self.checklist else [],
            "sources": [self.baseline.sources] + ([self.checklist.sources] if self.checklist else []),
            "design_context": self.design,
            "taste": bool(self.taste_note),
        }


def _strings(entry: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{CATALOG} {where}: '{key}' must be a list of strings")
    return tuple(value)


def _checklist(entry: dict[str, Any], where: str) -> dict[str, Any]:
    items = _strings(entry, "checklist", where)
    if not items:
        raise ConfigError(f"{CATALOG} {where}: 'checklist' must not be empty")
    return {
        "title": str(entry.get("title", where)),
        "checklist": items,
        "focus": str(entry.get("focus", "")),
        "sources": str(entry.get("sources", "")),
    }


@lru_cache(maxsize=1)
def load_ui_catalog() -> UICatalog:
    data = load_toml_data(CATALOG)
    baseline = Checklist(**_checklist(data.get("baseline", {}), "[baseline]"))
    domains: list[UIDomain] = []
    for entry in data.get("domains", []):
        did = entry.get("id")
        if not isinstance(did, str) or not did or did == NO_DOMAIN:
            raise ConfigError(f"{CATALOG}: every [[domains]] entry needs a string 'id' other than 'none'")
        where = f"domain '{did}'"
        domains.append(
            UIDomain(
                **_checklist(entry, where),
                id=did,
                task_phrases=_strings(entry, "task_phrases", where),
                task_words=_strings(entry, "task_words", where),
                npm_packages=_strings(entry, "npm_packages", where),
                unless_npm_packages=_strings(entry, "unless_npm_packages", where),
                files=_strings(entry, "files", where),
                fallback=entry.get("fallback", False) is True,
            )
        )
    spec = data.get("design_context", {})
    min_props = spec.get("min_custom_properties", 5)
    if not isinstance(min_props, int) or min_props < 1:
        raise ConfigError(f"{CATALOG} [design_context]: 'min_custom_properties' must be a positive integer")
    where = "[design_context]"
    design = DesignSpec(
        docs=_strings(spec, "docs", where),
        tokens=_strings(spec, "tokens", where),
        css_files=_strings(spec, "css_files", where),
        min_custom_properties=min_props,
        storybook=_strings(spec, "storybook", where),
        figma=_strings(spec, "figma", where),
        taste_skill=str(spec.get("taste_skill", "")),
        taste_note=str(spec.get("taste_note", "")),
        found_note=str(spec.get("found_note", "")),
        missing_note=str(spec.get("missing_note", "")),
        compact_found_note=str(spec.get("compact_found_note", "")),
        compact_missing_note=str(spec.get("compact_missing_note", "")),
    )
    return UICatalog(baseline=baseline, domains=tuple(domains), design=design)


@lru_cache(maxsize=1)
def domain_ids() -> tuple[str, ...]:
    """Domain ids, read without parsing the whole catalog: configuration validation runs in every
    hook process, and the packaged rules name domains. A test checks this against the full parse."""
    return tuple(re.findall(r'^id = "([^"]+)"', read_data_text(CATALOG), re.MULTILINE))


@cache
def _patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"(?<![\w-])(?:{p})(?![\w-])", re.IGNORECASE) for p in patterns)


def _first_match(task: str, patterns: tuple[str, ...]) -> str | None:
    match = next((m for p in _patterns(patterns) if (m := p.search(task))), None)
    return match.group(0) if match else None


def _has_package(packages: set[str], name: str) -> str | None:
    if name.endswith("/"):
        return next((p for p in sorted(packages) if p.startswith(name)), None)
    return name if name in packages else None


def detect_domains(npm_packages: set[str], has_file: Callable[[str], bool]) -> list[dict[str, str]]:
    """Domains the project's dependencies and files point to, in catalog order. Read-only.

    A `fallback` domain (a component library) counts only when no other domain is detected.
    """
    found: list[dict[str, str]] = []
    fallbacks: list[dict[str, str]] = []
    for domain in load_ui_catalog().domains:
        if any(_has_package(npm_packages, p) for p in domain.unless_npm_packages):
            continue
        hits = [hit for p in domain.npm_packages if (hit := _has_package(npm_packages, p))]
        hits += [f for f in domain.files if has_file(f)]
        if hits:
            (fallbacks if domain.fallback else found).append(
                {"id": domain.id, "evidence": ", ".join(hits[:2])}
            )
    return found or fallbacks


def choose_domain(task: str, config: Config, detected: list[dict[str, str]]) -> UIContext:
    """Pick the UI domain for a UI task: configuration, task phrases, task words, then the project."""
    catalog = load_ui_catalog()
    configured = config.get("ui", {}).get("domain", "") or ""

    def context(domain: UIDomain | None, reason: str, source: str) -> UIContext:
        return UIContext(
            domain.id if domain else None,
            domain.title if domain else None,
            reason,
            baseline=catalog.baseline,
            checklist=domain,
            source=source,
            detected=detected,
        )

    if configured == NO_DOMAIN:
        return context(None, 'none ([ui] domain = "none")', "config")
    if configured:
        chosen = catalog.get(str(configured))
        if chosen is None:  # validated with the configuration; kept safe here
            raise ConfigError(f"[ui] domain: unknown domain {configured!r}")
        return context(chosen, "set in [ui] domain", "config")
    # A phrase that names the product ("landing page") beats a generic word ("chart") anywhere.
    for kind in ("task_phrases", "task_words"):
        for domain in catalog.domains:
            hit = _first_match(task, getattr(domain, kind))
            if hit:
                return context(domain, f'the task mentions "{hit}"; set [ui] domain to change', "task")
    if detected:
        found = catalog.get(detected[0]["id"])
        if found is not None:
            reason = f"detected from {detected[0]['evidence']}; set [ui] domain to change"
            return context(found, reason, "project")
    return context(None, "none detected; set [ui] domain to pick one", "none")


# Test, story, and type-declaration siblings of a token file ("theme.test.ts") are not tokens.
_SECONDARY = re.compile(r"\.(?:test|spec|stories|story|d|min|map|bak|old)\.", re.IGNORECASE)
_CUSTOM_PROPERTY = re.compile(r"(?:^|[;{])\s*--[\w-]+\s*:", re.MULTILINE)
_THEME_BLOCK = re.compile(r"(?:^|[;}])\s*@theme\b", re.MULTILINE)
# Comments are left out before counting, so "/* no @theme here */" does not count.
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
MAX_PER_PATTERN = 3


MAX_NAME_LENGTH = 120


def safe_name(name: str) -> bool:
    """A file name that can appear in a brief: printable and short.

    A name with a newline (possible on Linux and macOS) could otherwise add lines to the brief
    that look like Susanoo's own instructions.
    """
    return len(name) <= MAX_NAME_LENGTH and name.isprintable()


def match_paths(root: Path, pattern: str) -> list[str]:
    """Root-relative paths matching a pattern, case-insensitively on every OS.

    Only the last path segment may contain a wildcard (not recursive). A trailing / matches
    folders only; other patterns match files only. The real file name is reported.
    """
    folders_only = pattern.endswith("/")
    parent_rel, _, name = pattern.rstrip("/").rpartition("/")
    parent = root / parent_rel if parent_rel else root
    try:
        entries = sorted(os.listdir(parent))
    except OSError:
        return []
    wanted = name.lower()
    hits = []
    for entry in entries:
        if not safe_name(entry) or not fnmatch.fnmatchcase(entry.lower(), wanted):
            continue
        path = parent / entry
        if folders_only != path.is_dir() or (not folders_only and not path.is_file()):
            continue
        if not folders_only and _SECONDARY.search(entry):
            continue
        hits.append(f"{parent_rel}/{entry}" if parent_rel else entry)
    # The plainest names first ("theme.ts" before "theme.config.ts"), then alphabetical.
    hits.sort(key=lambda h: (h.count("."), len(h), h))
    return [h + "/" if folders_only else h for h in hits[:MAX_PER_PATTERN]]


def _defines_theme(path: Path, minimum: int) -> bool:
    try:
        with path.open("rb") as fh:
            text = fh.read(CSS_READ_LIMIT).decode("utf-8", errors="replace")
    except OSError:
        return False
    text = _CSS_COMMENT.sub(" ", text)
    return bool(_THEME_BLOCK.search(text)) or len(_CUSTOM_PROPERTY.findall(text)) >= minimum


def detect_design_files(root: Path, npm_packages: set[str]) -> list[dict[str, str]]:
    """Design documents, token files, and Storybook in the project. Read-only; paths, not contents."""
    spec = load_ui_catalog().design
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, path: str) -> None:
        # The same file can match two patterns ("DESIGN.md" and "design.md" on Windows).
        key = os.path.normcase(os.path.realpath(root / path.rstrip("/")))
        if key not in seen:
            seen.add(key)
            found.append({"kind": kind, "path": path})

    for kind, patterns in (("docs", spec.docs), ("tokens", spec.tokens), ("storybook", spec.storybook)):
        for pattern in patterns:
            for path in match_paths(root, pattern):
                add(kind, path)
    for pattern in spec.css_files:
        for path in match_paths(root, pattern):
            if _defines_theme(root / path, spec.min_custom_properties):
                add("tokens", path)
    if not any(f["kind"] == "storybook" for f in found):
        hit = next((p for p in sorted(npm_packages) if p == "storybook" or p.startswith("@storybook/")), None)
        if hit:
            add("storybook", f"package.json ({hit})")
    return found


def describe_design(inventory: Inventory) -> tuple[list[str], str]:
    """Design context for the brief, and the taste skill's note when that skill is installed.

    Only usable Figma servers and plugins count, once per name.
    """
    spec = load_ui_catalog().design
    labels = {"docs": "", "tokens": "tokens: ", "storybook": "Storybook: "}
    items = [labels.get(f["kind"], "") + f["path"] for f in inventory.design_files]
    figma: dict[str, str] = {}
    for finding in inventory.findings:
        candidate = finding.kind in ("mcp_server", "plugin") and finding.status.usable
        if candidate and finding.name not in figma and any(n in finding.name.lower() for n in spec.figma):
            figma[finding.name] = "MCP server" if finding.kind == "mcp_server" else "plugin"
    items += [f"Figma: {name} ({kind})" for name, kind in figma.items()]
    taste = any(
        f.kind == "skill" and f.name == spec.taste_skill and f.status.usable for f in inventory.findings
    )
    return items, (spec.taste_note if taste and spec.taste_skill else "")


def design_notes(compact: bool = False) -> tuple[str, str]:
    """What the brief says when design context was found, and when none was."""
    spec = load_ui_catalog().design
    if compact:
        return spec.compact_found_note, spec.compact_missing_note
    return spec.found_note, spec.missing_note


def compact_design_line(items: list[str]) -> str:
    """The short brief's design-context line: at most five items, then a count."""
    found, missing = design_notes(compact=True)
    if not items:
        return f"Design context: {missing}"
    shown = "; ".join(items[:5]) + (f"; and {len(items) - 5} more" if len(items) > 5 else "")
    return f"Design context: {shown}. {found}"
