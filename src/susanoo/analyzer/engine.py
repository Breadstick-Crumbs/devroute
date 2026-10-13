"""Task Intelligence Engine: explainable, deterministic, configurable heuristics.

The engine matches configured signals against the task description, scores ten
dimensions on a 0-3 scale, and derives complexity, risk, and uncertainty as
independent composites. It never consults a model, so the same input and
configuration always produce the same assessment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from susanoo.analyzer.models import (
    LEVEL_NAMES,
    Assessment,
    CapabilityRequirement,
    CompositeScore,
    DimensionScore,
    ProjectContext,
    SignalMatch,
)
from susanoo.config import DIMENSIONS, Config

_WORD = re.compile(r"[\w'/+-]+")


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])(?:{pattern})(?![\w-])", re.IGNORECASE)


@dataclass
class _Contribution:
    signal: SignalMatch
    level: int


def match_signals(task: str, signals: Iterable[dict[str, Any]]) -> list[SignalMatch]:
    """Return signals whose patterns occur in ``task``, with the matched phrases."""
    found: list[SignalMatch] = []
    for signal in signals:
        phrases: list[str] = []
        for pattern in signal["patterns"]:
            for m in _compile(pattern).finditer(task):
                phrase = m.group(0).strip().lower()
                if phrase and phrase not in phrases:
                    phrases.append(phrase)
        if phrases:
            found.append(
                SignalMatch(
                    id=signal["id"],
                    label=signal.get("label", signal["id"]),
                    matches=phrases,
                    area=signal.get("area"),
                    kind=signal.get("kind"),
                )
            )
    return found


def _quote(phrases: list[str], limit: int = 3) -> str:
    shown = ", ".join(f"'{p}'" for p in phrases[:limit])
    return shown + (", ..." if len(phrases) > limit else "")


def _composite_level(score: float, thresholds: dict[str, float], names: tuple[str, ...]) -> str:
    """Map a 0-1 score to the highest named level whose threshold it reaches."""
    level = "low"
    for name in names:
        if score >= thresholds[name] - 1e-9:
            level = name
    return level


def assess(task: str, config: Config, context: ProjectContext | None = None) -> Assessment:
    """Assess a task description. Raises ``ValueError`` for an empty description."""
    text = " ".join(task.split())
    if not text:
        raise ValueError("task description is empty")

    signal_defs = {s["id"]: s for s in config.get("signals", [])}
    matches = match_signals(text, signal_defs.values())

    levels: dict[str, int] = dict.fromkeys(DIMENSIONS, 0)
    reasons: dict[str, list[str]] = {d: [] for d in DIMENSIONS}
    contributions: dict[str, list[_Contribution]] = {d: [] for d in DIMENSIONS}

    for dim, level in config["assessment"].get("baseline", {}).items():
        levels[dim] = level
        reasons[dim].append(f"baseline {level} for any code change")

    for match in matches:
        for dim, level in signal_defs[match.id].get("effects", {}).items():
            contributions[dim].append(_Contribution(match, level))
            if level > 0:
                reasons[dim].append(f"{match.label} ({_quote(match.matches)}) -> {level}")
            levels[dim] = max(levels[dim], level)

    # Several independent signals pushing the same dimension compound one step.
    stacking_level = config["assessment"].get("stacking_level", 2)
    for dim in DIMENSIONS:
        strong = {c.signal.id for c in contributions[dim] if c.level >= stacking_level}
        if len(strong) >= 2 and levels[dim] < 3:
            levels[dim] += 1
            reasons[dim].append(f"+1: {len(strong)} independent signals at level >= {stacking_level}")

    # Feature areas drive component count and parallelization opportunity.
    areas: list[str] = []
    for match in matches:
        if match.area and match.area not in areas:
            areas.append(match.area)
    if len(areas) >= 2:
        component_level = 3 if len(areas) >= 3 else 2
        if component_level > levels["affected_components"]:
            levels["affected_components"] = component_level
            reasons["affected_components"].append(
                f"{len(areas)} distinct feature areas: {', '.join(areas)} -> {component_level}"
            )
    if len(areas) >= 3:
        parallel_level = 3 if len(areas) >= 4 else 2
        if parallel_level > levels["parallelization"]:
            levels["parallelization"] = parallel_level
            reasons["parallelization"].append(
                f"{len(areas)} feature areas can be developed as separate units -> {parallel_level}"
            )

    # Small-change caps apply only when nothing indicates a larger scope.
    has_scope_signal = any(m.kind == "scope" for m in matches)
    for match in matches:
        caps = signal_defs[match.id].get("caps", {})
        if not caps:
            continue
        if has_scope_signal:
            continue
        for dim, cap in caps.items():
            if levels[dim] > cap:
                levels[dim] = cap
                reasons[dim].append(f"capped at {cap}: {match.label} ({_quote(match.matches)})")

    context_notes = _apply_context(context, config, levels, reasons)

    dimensions = {d: DimensionScore(d, levels[d], reasons[d]) for d in DIMENSIONS}
    confidence, confidence_notes = _confidence(text, matches, config)

    return Assessment(
        task=text,
        dimensions=dimensions,
        complexity=_complexity(dimensions, config),
        risk=_risk(dimensions, config),
        uncertainty=_uncertainty(dimensions, confidence, config),
        confidence=confidence,
        confidence_notes=confidence_notes,
        signals=matches,
        areas=areas,
        requirements=_requirements(matches, signal_defs),
        context_notes=context_notes,
    )


def _apply_context(
    context: ProjectContext | None,
    config: Config,
    levels: dict[str, int],
    reasons: dict[str, list[str]],
) -> list[str]:
    notes: list[str] = []
    if context is None:
        return notes
    if context.has_test_framework is False and levels["implementation_complexity"] > 0:
        if levels["testing_difficulty"] < 3:
            levels["testing_difficulty"] += 1
            reasons["testing_difficulty"].append("+1: no automated test framework detected in the project")
        notes.append(
            "No automated test framework was detected; verification will need setup or manual checks."
        )
    if context.is_git_repo is False:
        if levels["irreversibility"] < 3:
            levels["irreversibility"] += 1
            reasons["irreversibility"].append("+1: project is not under version control")
        notes.append("The project is not a git repository; changes are harder to review and roll back.")
    large = config.get("context", {}).get("large_repo_files", 2000)
    if context.source_files is not None and context.source_files >= large:
        if levels["architectural_complexity"] >= 2 and levels["affected_components"] < 3:
            levels["affected_components"] += 1
            reasons["affected_components"].append(
                f"+1: large repository ({context.source_files} source files) with architectural scope"
            )
        notes.append(f"Large repository ({context.source_files} source files): investigate before editing.")
    return notes


def _confidence(text: str, matches: list[SignalMatch], config: Config) -> tuple[str, list[str]]:
    words = len(_WORD.findall(text))
    notes: list[str] = []
    if words < config["assessment"].get("min_words", 3):
        notes.append(f"The description is very short ({words} words).")
    if not matches:
        notes.append("No recognizable engineering signals were found in the description.")
    if notes:
        notes.append(
            "Clarify scope, affected components, and expected behavior before relying on this assessment."
        )
        return "low", notes
    if len(matches) == 1:
        return "moderate", ["Only one signal matched; the assessment rests on limited evidence."]
    return "high", []


def _complexity(dimensions: dict[str, DimensionScore], config: Config) -> CompositeScore:
    section = config["complexity"]
    weights: dict[str, float] = section["weights"]
    total = sum(weights.values())
    score = sum(weights[d] * dimensions[d].level for d in weights) / (3 * total)
    level = _composite_level(score, section["thresholds"], ("moderate", "high"))
    drivers = [f"{d}={dimensions[d].label}" for d in weights if dimensions[d].level >= 2]
    return CompositeScore(level, round(score, 3), drivers)


def _risk(dimensions: dict[str, DimensionScore], config: Config) -> CompositeScore:
    section = config["risk"]
    dims: list[str] = section["dimensions"]
    values = [dimensions[d].level / 3 for d in dims]
    max_weight = float(section["max_weight"])
    score = max_weight * max(values) + (1 - max_weight) * (sum(values) / len(values))
    level = _composite_level(score, section["thresholds"], ("moderate", "high", "critical"))
    severe = [d for d in dims if dimensions[d].level == 3]
    drivers = [f"{d}={dimensions[d].label}" for d in dims if dimensions[d].level >= 2]
    if len(severe) >= section.get("critical_severe_dimensions", 2) and level != "critical":
        level = "critical"
        drivers.append(f"{len(severe)} risk dimensions at the highest level")
    return CompositeScore(level, round(score, 3), drivers)


def _uncertainty(dimensions: dict[str, DimensionScore], confidence: str, config: Config) -> CompositeScore:
    section = config["uncertainty"]
    score = dimensions["technical_uncertainty"].level / 3
    drivers = []
    if dimensions["technical_uncertainty"].level >= 1:
        drivers.append(f"technical_uncertainty={dimensions['technical_uncertainty'].label}")
    if confidence == "low":
        score += float(section.get("low_confidence_penalty", 0.5))
        drivers.append("low assessment confidence (task description lacks detail)")
    score = min(score, 1.0)
    level = _composite_level(score, section["thresholds"], ("moderate", "high"))
    return CompositeScore(level, round(score, 3), drivers)


def _requirements(
    matches: list[SignalMatch], signal_defs: dict[str, dict[str, Any]]
) -> list[CapabilityRequirement]:
    reqs: dict[str, CapabilityRequirement] = {}
    for match in matches:
        definition = signal_defs[match.id]
        for level, key in (("required", "requires"), ("recommended", "recommends")):
            for capability in definition.get(key, []):
                req = reqs.setdefault(capability, CapabilityRequirement(capability, level))
                if level == "required":
                    req.level = "required"
                req.reasons.append(f"{match.label}")
    return list(reqs.values())


__all__ = ["LEVEL_NAMES", "assess", "match_signals"]
