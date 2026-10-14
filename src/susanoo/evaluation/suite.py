"""Reproducible routing evaluation against a labeled task suite.

The bundled suite's labels are the maintainers' expectations of a sensible route,
not ground truth. The evaluation is deterministic: it measures whether routing
behavior matches those expectations and catches unintended changes when heuristics
are edited. It does not measure whether a workflow produces better software; see
docs/evaluation.md for the outcome-comparison protocol.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from susanoo.analyzer import assess
from susanoo.config import PROFILE_ORDER, Config, ConfigError, read_data_text
from susanoo.router import Profile, route

RISK_ORDER = ("low", "moderate", "high", "critical")


@dataclass
class CaseResult:
    id: str
    task: str
    expected_profile: str
    actual_profile: str
    risk: str
    min_risk: str | None
    passed: bool
    controls: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class SuiteResult:
    suite: str
    total: int
    profile_agreement: float
    risk_floor_violations: int
    confusion: dict[str, dict[str, int]]
    cases: list[CaseResult]

    @property
    def failures(self) -> list[CaseResult]:
        return [c for c in self.cases if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failures"] = [c.id for c in self.failures]
        return data


def load_suite(path: Path | None = None) -> tuple[str, list[dict[str, Any]]]:
    if path is None:
        text, name = read_data_text("suites", "routing-v1.toml"), "routing-v1 (bundled)"
    else:
        try:
            text, name = path.read_text(encoding="utf-8"), str(path)
        except OSError as exc:
            raise ConfigError(f"{path}: cannot read suite: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{name}: invalid TOML: {exc}") from exc
    cases = data.get("cases", [])
    for i, case in enumerate(cases):
        if not isinstance(case.get("task"), str) or case.get("expected") not in PROFILE_ORDER:
            raise ConfigError(f"{name}: case {i + 1} needs 'task' and 'expected' (fast|standard|deep)")
        if case.get("min_risk") not in (None, *RISK_ORDER):
            raise ConfigError(f"{name}: case {i + 1} has invalid min_risk")
        controls = case.get("controls", [])
        if not isinstance(controls, list) or not all(isinstance(c, str) for c in controls):
            raise ConfigError(f"{name}: case {i + 1} has invalid controls (a list of control ids)")
    return name, cases


def run_suite(config: Config, profiles: dict[str, Profile], path: Path | None = None) -> SuiteResult:
    name, cases = load_suite(path)
    confusion = {e: dict.fromkeys(PROFILE_ORDER, 0) for e in PROFILE_ORDER}
    results: list[CaseResult] = []
    agree = 0
    violations = 0
    for i, case in enumerate(cases):
        assessment = assess(case["task"], config)
        decision = route(assessment, profiles, config)
        expected = case["expected"]
        actual = decision.profile
        confusion[expected][actual] += 1
        problems: list[str] = []
        if actual == expected:
            agree += 1
        else:
            problems.append(f"expected {expected.upper()}, routed {actual.upper()}")
        min_risk = case.get("min_risk")
        below_floor = False
        if min_risk and RISK_ORDER.index(assessment.risk.level) < RISK_ORDER.index(min_risk):
            below_floor = True
            problems.append(f"risk {assessment.risk.level} is below the expected minimum {min_risk}")
        expected_controls: list[str] = case.get("controls", [])
        applied = {c.id for c in decision.controls}
        missing = [c for c in expected_controls if c not in applied]
        if missing:
            below_floor = True
            problems.append(f"missing safety control(s): {', '.join(missing)}")
        if below_floor:
            violations += 1
        results.append(
            CaseResult(
                id=case.get("id", f"case-{i + 1}"),
                task=case["task"],
                expected_profile=expected,
                actual_profile=actual,
                risk=assessment.risk.level,
                min_risk=min_risk,
                passed=not problems,
                controls=expected_controls,
                problems=problems,
            )
        )
    total = len(cases)
    return SuiteResult(
        suite=name,
        total=total,
        profile_agreement=round(agree / total, 3) if total else 0.0,
        risk_floor_violations=violations,
        confusion=confusion,
        cases=results,
    )
