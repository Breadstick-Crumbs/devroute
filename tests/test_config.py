from __future__ import annotations

from pathlib import Path

import pytest

from devroute.config import ConfigError, load_config, merge, validate
from tests.conftest import write


def test_defaults_are_valid(config):
    validate({k: v for k, v in config.items() if k != "_sources"})
    assert config["_sources"] == ["defaults"]
    assert len(config["signals"]) >= 20


def test_project_override_merges_tables_and_signals_by_id(tmp_path: Path):
    write(
        tmp_path,
        ".devroute/config.toml",
        (
            "schema_version = 1\n"
            "[risk]\nthresholds = { moderate = 0.2, high = 0.5, critical = 0.8 }\n"
            '[[signals]]\nid = "notifications"\nenabled = false\n'
            '[[signals]]\nid = "payments"\neffects = { security_privacy = 2 }\n'
            '[[signals]]\nid = "ledger"\npatterns = ["ledger"]\neffects = { data_integrity = 3 }\n'
        ),
    )
    config = load_config(tmp_path)
    ids = {s["id"]: s for s in config["signals"]}
    assert "notifications" not in ids
    assert ids["payments"]["effects"] == {
        "security_privacy": 2,
        "data_integrity": 3,
        "external_dependencies": 3,
        "testing_difficulty": 2,
    }
    assert ids["payments"]["patterns"]  # untouched fields survive
    assert "ledger" in ids
    assert config["risk"]["thresholds"]["high"] == 0.5
    assert config["risk"]["max_weight"] == 0.75  # sibling keys survive


def test_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}, "signals": [{"id": "x", "patterns": ["x"]}]}
    merge(base, {"a": {"c": 2}, "signals": [{"id": "x", "label": "X"}]})
    assert base == {"a": {"b": 1}, "signals": [{"id": "x", "patterns": ["x"]}]}


@pytest.mark.parametrize(
    "snippet, message",
    [
        ("[complexity]\nthresholds = { moderate = 0.8, high = 0.5 }\n", "increasing"),
        ('[[signals]]\nid = "bad"\npatterns = ["x"]\neffects = { nonsense = 2 }\n', "unknown dimension"),
        (
            '[[signals]]\nid = "bad"\npatterns = ["x"]\neffects = { security_privacy = 7 }\n',
            "between 0 and 3",
        ),
        ('[routing]\ncritical_risk_floor = "turbo"\n', "critical_risk_floor"),
        ('[[signals]]\npatterns = ["x"]\n', "needs an 'id'"),
    ],
)
def test_invalid_overrides_are_rejected(tmp_path: Path, snippet: str, message: str):
    write(tmp_path, ".devroute/config.toml", "schema_version = 1\n" + snippet)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_invalid_toml_reports_path(tmp_path: Path):
    write(tmp_path, ".devroute/config.toml", "this is = = not toml")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(tmp_path)
