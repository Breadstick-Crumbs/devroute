"""UI domains and checklists (ADR 0008): which kind of product a UI task is for, and what to check.

The domain is a heuristic: `[ui] domain` in configuration, then phrases and then words in the task,
then project detection (dependencies and files, read during discovery). Briefs always say which
source chose it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import Any

from susanoo.config import Config, ConfigError, load_toml_data, read_data_text

NO_DOMAIN = "none"  # `[ui] domain = "none"`: generic baseline only, no detection
CATALOG = "ui_domains.toml"


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
class UICatalog:
    baseline: Checklist
    domains: tuple[UIDomain, ...]

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
    return UICatalog(baseline=baseline, domains=tuple(domains))


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
