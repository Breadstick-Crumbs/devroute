from __future__ import annotations

from pathlib import Path

import pytest

from susanoo.config import ConfigError
from susanoo.evaluation import RecordError, RecordStore, run_suite, summarize
from tests.conftest import write


def test_store_roundtrip_and_resolution(tmp_path: Path):
    store = RecordStore(tmp_path)
    store.append("routes", {"id": "dr-20260101-000000-aaaa", "profile": "fast"})
    store.append("routes", {"id": "dr-20260102-000000-bbbb", "profile": "deep"})
    assert store.resolve_route("latest")["profile"] == "deep"
    assert store.resolve_route("dr-20260101")["profile"] == "fast"
    with pytest.raises(RecordError, match="matches 2"):
        store.resolve_route("dr-2026")
    with pytest.raises(RecordError, match="no route record"):
        store.resolve_route("nope")


def test_corrupt_lines_are_skipped(tmp_path: Path):
    store = RecordStore(tmp_path)
    store.append("routes", {"id": "a"})
    with store.path("routes").open("a", encoding="utf-8") as fh:
        fh.write("{truncated\n[1, 2]\n")
    assert [r["id"] for r in store.read("routes")] == ["a"]
    assert store.skipped_lines == 2


def test_empty_store_explains_how_to_create_records(tmp_path: Path):
    with pytest.raises(RecordError, match="susanoo init"):
        RecordStore(tmp_path).resolve_route("latest")


def _routes(profile: str, n: int, overridden: bool = False) -> list[dict]:
    return [
        {"id": f"{profile}-{i}", "profile": profile, "recommended": "standard", "overridden": overridden}
        for i in range(n)
    ]


def test_summary_metrics():
    routes = _routes("fast", 2)
    outcomes = [
        {"record_id": "fast-0", "status": "failed", "duration_minutes": 10},
        {
            "record_id": "fast-0",
            "status": "completed",
            "duration_minutes": 4,
            "human_interventions": 1,
            "tokens": 1000,
            "cost_usd": 0.5,
        },
    ]
    evidence = [
        {"record_id": "fast-0", "source": "executed", "status": "passed"},
        {"record_id": "fast-0", "source": "executed", "status": "failed"},
        {"record_id": "fast-0", "source": "executed", "status": "skipped"},
        {"record_id": "fast-1", "source": "reported", "status": "passed"},
    ]
    [s] = summarize(routes, outcomes, evidence).profiles
    assert (s.routes, s.with_outcome, s.completed) == (2, 1, 1)  # latest outcome wins
    assert s.mean_minutes == 4
    assert (s.executed_checks, s.executed_failures, s.reported_checks) == (2, 1, 1)
    assert s.total_tokens == 1000 and s.total_cost_usd == 0.5


def test_no_recommendations_without_enough_data():
    report = summarize(
        _routes("fast", 3), [{"record_id": f"fast-{i}", "status": "failed"} for i in range(3)], []
    )
    assert report.insights == []
    assert "at least 10" in report.notes[0]


def test_recommendations_with_enough_data():
    routes = _routes("fast", 12, overridden=True)
    outcomes = [
        {"record_id": f"fast-{i}", "status": "failed" if i < 6 else "completed", "human_interventions": 3}
        for i in range(12)
    ]
    report = summarize(routes, outcomes, [], min_samples=10)
    observations = " ".join(i.observation for i in report.insights)
    assert "50%" in observations
    assert "human interventions" in observations
    assert "overridden" in observations


def test_bundled_suite_runs_deterministically(config, profiles):
    first = run_suite(config, profiles)
    second = run_suite(config, profiles)
    assert first.to_dict() == second.to_dict()
    assert first.total >= 30
    assert first.risk_floor_violations == 0
    assert first.profile_agreement >= 0.9


def test_custom_suite_validation(tmp_path: Path, config, profiles):
    bad = write(tmp_path, "suite.toml", '[[cases]]\ntask = "x"\nexpected = "medium"\n')
    with pytest.raises(ConfigError, match="expected"):
        run_suite(config, profiles, bad)
    good = write(tmp_path, "ok.toml", '[[cases]]\nid = "t"\ntask = "Fix typo in README"\nexpected = "deep"\n')
    result = run_suite(config, profiles, good)
    assert result.profile_agreement == 0.0
    assert result.failures[0].problems == ["expected DEEP, routed FAST"]
