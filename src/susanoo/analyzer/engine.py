"""Task Intelligence Engine: explainable, deterministic, configurable heuristics.

The engine matches configured signals against the task description, scores ten
dimensions on a 0-3 scale, and derives complexity, risk, and uncertainty as
independent composites. It never consults a model, so the same input and
configuration always produce the same assessment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from susanoo.analyzer.models import (
    LEVEL_NAMES,
    Assessment,
    CapabilityRequirement,
    CompositeScore,
    DimensionScore,
    Floor,
    ProjectContext,
    SignalMatch,
)
from susanoo.config import DIMENSIONS, Config

_WORD = re.compile(r"[\w'/+-]+")


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])(?:{pattern})(?![\w-])", re.IGNORECASE)


@lru_cache(maxsize=64)
def _compile_plain(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Words that start a new clause; a negated target never runs across them.
_CLAUSE_WORDS = "and|but|then|or|plus|we|you|when|while|so|except|until|before|after|which|that"


def negation_patterns(negation: dict[str, Any]) -> list[str]:
    """``[assessment.negation] patterns`` with ``{target}`` and ``{location}`` expanded."""
    location = "(?:" + "|".join(negation.get("locations", [])) + ")"
    # A word may contain . / - only between letters, so a target never runs past a sentence end.
    word = rf"(?!(?:{_CLAUSE_WORDS})(?![\w-]))\w+(?:[./-]\w+)*"
    filler = int(negation.get("filler", 3))
    target = rf"(?:(?:the|any|our|its|this|their|your) )?(?:{word} ){{0,{filler}}}{location}"
    return [
        p.replace("{target}", target).replace("{location}", location) for p in negation.get("patterns", [])
    ]


def negated_spans(
    text: str, negation: dict[str, Any], keep_patterns: Iterable[str] = ()
) -> list[tuple[int, int]]:
    """Character spans of ``text`` that an instruction says to leave alone.

    A span ends at its code-location noun, so text after it always counts. Spans that
    contain a match of ``keep_patterns`` (destructive or production work) are never returned.
    """
    if not negation.get("locations"):
        return []
    keep = [(m.start(), m.end()) for p in keep_patterns for m in _compile(p).finditer(text)]
    spans: list[tuple[int, int]] = []
    for pattern in negation_patterns(negation):
        for m in _compile(pattern).finditer(text):
            if not any(start < m.end() and m.start() < end for start, end in keep):
                spans.append((m.start(), m.end()))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


_CONNECTOR_BEFORE = re.compile(r"(?:\s*[,;:])?\s*(?:(?<![\w-])(?:and|but|while)\s*)?$", re.IGNORECASE)
_CONNECTOR_AFTER = re.compile(r"^\s*[,;:]?\s*")
_SECOND_SENTENCE = re.compile(r"[.!?]\s+\S")


def _negation_holds(
    text: str, spans: list[tuple[int, int]], matched: set[str], negation: dict[str, Any]
) -> bool:
    """Whether ignoring ``spans`` is safe enough: see ``[assessment.negation]`` in defaults.toml.

    Outside the spans, a ``requires_signals`` signal must match, no ``blocking_signals`` may
    match, and, without each span's connecting comma or "and"/"but", the text must be a
    single clause.
    """
    required = set(negation.get("requires_signals", []))
    if required and not required & matched:
        return False
    if set(negation.get("blocking_signals", [])) & matched:
        return False
    pieces: list[str] = []
    last = 0
    for start, end in spans:
        before = text[last:start]
        if last:
            before = _CONNECTOR_AFTER.sub("", before)
        pieces.append(_CONNECTOR_BEFORE.sub("", before))
        last = end
    pieces.append(_CONNECTOR_AFTER.sub("", text[last:]))
    rest = " ".join(p.strip() for p in pieces if p.strip()).rstrip(".!? ")
    clause = negation.get("clause")
    if clause and _compile_plain(clause).search(rest):
        return False
    return not _SECOND_SENTENCE.search(rest)


def _mask(text: str, spans: Iterable[tuple[int, int]]) -> str:
    """``text`` with the given spans blanked out (lengths and positions preserved)."""
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


# One clause only: a comma, semicolon, colon, question mark, or line break ends presentational.
_CLAUSE_BREAK = re.compile(r"[,;:?\n]|\.\s")
_HEX_COLOR = re.compile(r"#[0-9a-f]{3,8}")


def _keep_matches(task: str, patterns: Iterable[str]) -> bool:
    """Whether a `keep` pattern matches, also with hyphens read as spaces ("Stripe-hosted")."""
    variants = (task, task.replace("-", " "))
    return any(re.search(rf"(?<!\w)(?:{p})(?!\w)", v, re.IGNORECASE) for p in patterns for v in variants)


def _words(task: str, phrases: Iterable[str]) -> list[str]:
    text = " " + " ".join(task.lower().split()) + " "
    for phrase in sorted(phrases, key=len, reverse=True):
        text = text.replace(f" {phrase} ", " " + phrase.replace(" ", "_") + " ")
    return text.split()


def _visual_only(words: list[str], verbs: set[str], config: dict[str, Any], require_appearance: bool) -> bool:
    """Read the task word by word against the closed vocabulary (linear, no backtracking)."""
    if words and words[0] == "please":
        words = words[1:]
    if not words or words[0] not in verbs:
        return False
    vocab = {
        key: {str(w).lower() for w in config.get(key, [])}
        for key in (
            "determiners",
            "prepositions",
            "ui_nouns",
            "appearance",
            "platform",
            "subjects",
            "reject_words",
        )
    }
    plain = vocab["determiners"] | vocab["prepositions"] | vocab["appearance"] | vocab["platform"]
    appearance = words[0] in vocab["appearance"]
    saw_ui = False
    # One page name directly before the first UI noun ("the settings page"); after the object, a
    # free word could change behavior ("to a single page").
    free_left = 1
    for i, word in enumerate(words[1:], start=1):
        following = words[i + 1] if i + 1 < len(words) else ""
        if word in vocab["reject_words"]:
            return False
        if word in vocab["ui_nouns"]:
            saw_ui = True
            appearance = appearance or word in vocab["appearance"]
        elif word in plain or _HEX_COLOR.fullmatch(word):
            appearance = appearance or word in vocab["appearance"] or bool(_HEX_COLOR.fullmatch(word))
        elif word in vocab["subjects"] and (following in vocab["ui_nouns"] or following in vocab["subjects"]):
            pass
        elif free_left and not saw_ui and following in vocab["ui_nouns"]:
            free_left -= 1
        else:
            return False
    return saw_ui and (appearance or not require_appearance)


def presentational_matches(
    text: str, matches: list[SignalMatch], config: dict[str, Any]
) -> list[SignalMatch]:
    """Signal matches to ignore because the whole task only changes how something looks.

    Ignoring can only lower risk, so this is narrow (see `[assessment.presentational]`): a short,
    single-clause task built only from a closed vocabulary, where every phrase the signal matched
    is a rule verb or a payment-page word, and no `keep` word appears anywhere.
    """
    if not config or not matches:
        return []
    task = text.strip().rstrip(".!")
    words_limit = int(config.get("max_words", 20))
    if len(task.split()) > words_limit or _CLAUSE_BREAK.search(task):
        return []
    if _keep_matches(task, config.get("keep", [])):
        return []
    words = _words(task, config.get("phrases", []))
    subjects = {str(w).lower() for w in config.get("subjects", [])}
    ignored: list[SignalMatch] = []
    for rule in config.get("rules", []):
        verbs = {str(v).lower() for v in rule.get("verbs", [])}
        targets = [m for m in matches if m.id in rule.get("signals", []) and m not in ignored]
        # Every phrase the signal matched must be a verb or a payment-page word of this rule:
        # "redesign ... with microservices" keeps the architectural signal.
        targets = [m for m in targets if all(p in verbs or p in subjects for p in m.matches)]
        if targets and _visual_only(words, verbs, config, bool(rule.get("require_appearance"))):
            ignored += targets
    return ignored


def _caps_allowed(text: str, signal: dict[str, Any]) -> bool:
    """Whether the whole task fits one of the signal's ``caps_only_if`` shapes (if it has any)."""
    shapes = signal.get("caps_only_if")
    if not shapes:
        return True
    task = text.strip().rstrip(".")
    return any(_compile_plain(shape).fullmatch(task) for shape in shapes)


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


def assess(
    task: str, config: Config, context: ProjectContext | None = None, floor: Floor | None = None
) -> Assessment:
    """Assess a task description. Raises ``ValueError`` for an empty description.

    ``floor`` is the assessment of the user's own message when ``task`` is Claude's description
    of it: no dimension ends lower than the floor's, and the floor's signals and areas count.
    """
    text = " ".join(task.split())
    if not text:
        raise ValueError("task description is empty")

    signal_defs = {s["id"]: s for s in config.get("signals", [])}
    # Line breaks and bullets end a sentence, so nothing (negation, test shapes) runs across them.
    lines = " ".join(re.sub(r"\s*[\r\n]+\s*(?:[-*•]\s+)?", ". ", task.strip()).split())
    # An instruction to leave a code location alone does not count as signals, but only when
    # the rest of the task is one clause holding a clearly trivial edit (see `_negation_holds`).
    negation = config["assessment"].get("negation", {})
    keep = [
        p for sid in negation.get("keep_signals", []) for p in signal_defs.get(sid, {}).get("patterns", [])
    ]
    spans = negated_spans(lines, negation, keep)
    scanned = _mask(lines, spans)
    matches = match_signals(scanned, signal_defs.values())
    if spans and not _negation_holds(lines, spans, {m.id for m in matches}, negation):
        spans, scanned = [], lines
        matches = match_signals(scanned, signal_defs.values())
    negated = [lines[start:end].strip() for start, end in spans]
    # A purely visual edit ("Change the checkout button color") does not count as payment logic
    # or as an architectural change.
    ignored = presentational_matches(lines, matches, config["assessment"].get("presentational", {}))
    matches = [m for m in matches if m not in ignored]
    presentational = [f"{m.label} ({_quote(m.matches)})" for m in ignored]

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
    _area_levels(areas, levels, reasons)

    # Small-change caps apply only when nothing indicates a larger scope.
    has_scope_signal = any(m.kind == "scope" for m in matches)
    for match in matches:
        caps = signal_defs[match.id].get("caps", {})
        if not caps:
            continue
        if has_scope_signal:
            continue
        matched_ids = {m.id for m in matches}
        blocked_by = set(signal_defs[match.id].get("caps_unless_signals", [])) & matched_ids
        needed = signal_defs[match.id].get("caps_if_signals", [])
        if (
            blocked_by
            or (needed and not set(needed) & matched_ids)
            or not _caps_allowed(lines, signal_defs[match.id])
        ):
            for dim, cap in caps.items():
                if levels[dim] > cap:
                    reasons[dim].append(f"not capped by {match.label}: the task is more than that")
            continue
        for dim, cap in caps.items():
            if levels[dim] > cap:
                levels[dim] = cap
                reasons[dim].append(f"capped at {cap}: {match.label} ({_quote(match.matches)})")

    if floor is not None:
        # After caps and before context: the floor only raises, and context counts once.
        _apply_floor(floor, signal_defs, matches, areas, levels, reasons)

    base_levels = dict(levels)
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
        negated=negated,
        presentational=presentational,
        base_levels=base_levels,
    )


def _area_levels(areas: list[str], levels: dict[str, int], reasons: dict[str, list[str]]) -> None:
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


def _apply_floor(
    floor: Floor,
    signal_defs: dict[str, dict[str, Any]],
    matches: list[SignalMatch],
    areas: list[str],
    levels: dict[str, int],
    reasons: dict[str, list[str]],
) -> None:
    """Raise everything to at least the user's message (see :class:`Floor`). Never lowers."""
    for dim in DIMENSIONS:
        level = floor.levels.get(dim, 0)
        if level > levels[dim]:
            levels[dim] = level
            reasons[dim].append(f"the user's message -> {level}")
    known = {m.id for m in matches}
    for sid in floor.signals:
        definition = signal_defs.get(sid)
        if definition is None or sid in known:
            continue
        known.add(sid)
        matches.append(
            SignalMatch(
                id=sid,
                label=definition.get("label", sid),
                matches=["(in the user's message)"],
                area=definition.get("area"),
                kind=definition.get("kind"),
            )
        )
    for area in floor.areas:
        if area not in areas:
            areas.append(area)
    _area_levels(areas, levels, reasons)


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


def without_vagueness(assessment: Assessment, config: Config) -> Assessment:
    """``assessment`` without the low-confidence uncertainty penalty.

    For a floor: a vague message ("ok", "why is this slow?") is not a risk, and the route built on
    it gets its own confidence from Claude's description.
    """
    if assessment.confidence != "low":
        return assessment
    return replace(
        assessment,
        confidence="moderate",
        uncertainty=_uncertainty(assessment.dimensions, "moderate", config),
    )


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
