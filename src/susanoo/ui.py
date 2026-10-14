"""UI domains and checklists (ADR 0008): which kind of product a UI task is for, and what to check.

The domain is a heuristic: `[ui] domain` in configuration, then words in the task, then project
detection (dependencies and files, read during discovery). Briefs always say which source chose it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from susanoo.config import Config, ConfigError, load_toml_data

NO_DOMAIN = "none"  # `[ui] domain = "none"`: generic baseline only, no detection


@dataclass(frozen=True)
class Checklist:
    title: str
    checklist: tuple[str, ...]
    focus: str
    sources: str


@dataclass(frozen=True)
class UIDomain(Checklist):
    id: str = ""
    task_words: tuple[re.Pattern[str], ...] = ()
    npm_packages: tuple[str, ...] = ()
    unless_npm_packages: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


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
    detected: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "title": self.title,
            "reason": self.reason,
            "baseline": list(self.baseline.checklist),
            "checklist": list(self.checklist.checklist) if self.checklist else [],
            "sources": [self.baseline.sources] + ([self.checklist.sources] if self.checklist else []),
        }


def _strings(entry: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"ui_domains.toml {where}: '{key}' must be a list of strings")
    return tuple(value)


def _checklist(entry: dict[str, Any], where: str) -> dict[str, Any]:
    items = _strings(entry, "checklist", where)
    if not items:
        raise ConfigError(f"ui_domains.toml {where}: 'checklist' must not be empty")
    return {
        "title": str(entry.get("title", where)),
        "checklist": items,
        "focus": str(entry.get("focus", "")),
        "sources": str(entry.get("sources", "")),
    }


@lru_cache(maxsize=1)
def load_ui_catalog() -> UICatalog:
    data = load_toml_data("ui_domains.toml")
    baseline = Checklist(**_checklist(data.get("baseline", {}), "[baseline]"))
    domains: list[UIDomain] = []
    for entry in data.get("domains", []):
        did = entry.get("id")
        if not isinstance(did, str) or not did or did == NO_DOMAIN:
            raise ConfigError(
                "ui_domains.toml: every [[domains]] entry needs a string 'id' other than 'none'"
            )
        where = f"domain '{did}'"
        words = tuple(
            re.compile(rf"(?<![\w-])(?:{w})(?![\w-])", re.IGNORECASE)
            for w in _strings(entry, "task_words", where)
        )
        domains.append(
            UIDomain(
                **_checklist(entry, where),
                id=did,
                task_words=words,
                npm_packages=_strings(entry, "npm_packages", where),
                unless_npm_packages=_strings(entry, "unless_npm_packages", where),
                files=_strings(entry, "files", where),
            )
        )
    return UICatalog(baseline=baseline, domains=tuple(domains))


def domain_ids() -> list[str]:
    return [d.id for d in load_ui_catalog().domains]


def _has_package(packages: set[str], name: str) -> str | None:
    if name.endswith("/"):
        return next((p for p in sorted(packages) if p.startswith(name)), None)
    return name if name in packages else None


def detect_domains(npm_packages: set[str], has_file: Any) -> list[dict[str, str]]:
    """Domains the project's dependencies and files point to, in catalog order. Read-only."""
    found: list[dict[str, str]] = []
    for domain in load_ui_catalog().domains:
        if any(_has_package(npm_packages, p) for p in domain.unless_npm_packages):
            continue
        hits = [hit for p in domain.npm_packages if (hit := _has_package(npm_packages, p))]
        hits += [f for f in domain.files if has_file(f)]
        if hits:
            found.append({"id": domain.id, "evidence": ", ".join(hits[:2])})
    return found


def choose_domain(task: str, config: Config, detected: list[dict[str, str]]) -> UIContext:
    """Pick the UI domain for a UI task: configuration, then task words, then project detection."""
    catalog = load_ui_catalog()
    configured = str(config.get("ui", {}).get("domain", "") or "")

    def context(domain: UIDomain | None, reason: str) -> UIContext:
        return UIContext(
            domain.id if domain else None,
            domain.title if domain else None,
            reason,
            baseline=catalog.baseline,
            checklist=domain,
            detected=detected,
        )

    if configured == NO_DOMAIN:
        return context(None, 'none ([ui] domain = "none")')
    if configured:
        chosen = catalog.get(configured)
        if chosen is None:  # validated with the configuration; kept safe here
            raise ConfigError(f"[ui] domain: unknown domain '{configured}'")
        return context(chosen, "set in [ui] domain")
    for domain in catalog.domains:
        match = next((m for p in domain.task_words if (m := p.search(task))), None)
        if match:
            return context(domain, f'the task mentions "{match.group(0)}"; set [ui] domain to change')
    if detected:
        found = catalog.get(detected[0]["id"])
        if found is not None:
            return context(found, f"detected from {detected[0]['evidence']}; set [ui] domain to change")
    return context(None, "none detected; set [ui] domain to pick one")
