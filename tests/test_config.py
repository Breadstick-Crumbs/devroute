from __future__ import annotations

from pathlib import Path

import pytest

from susanoo.config import ConfigError, load_config, merge, validate
from tests.conftest import write


def test_defaults_are_valid(config):
    validate({k: v for k, v in config.items() if k != "_sources"})
    assert config["_sources"] == ["defaults"]
    assert len(config["signals"]) >= 20


def test_project_override_merges_tables_and_signals_by_id(tmp_path: Path):
    write(
        tmp_path,
        ".susanoo/config.toml",
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
        ("[assessment.negation]\npatterns = ['(unclosed']\n", "invalid regular expression"),
        ("[assessment.negation]\nfiller = -1\n", "from 0 to 6"),
        ("[assessment.negation]\nkeep_signals = ['nope']\n", "unknown signal"),
        ('[[signals]]\nid = "bad"\npatterns = ["x"]\ncaps_only_if = "x"\n', "list of strings"),
        (
            '[[verification]]\nid = "dependency_audit"\ncapability = "dependency_audit"\n'
            'escalate = [{ signal = "nope", level = "recommended" }]\n',
            "unknown escalate signal",
        ),
        (
            '[[verification]]\nid = "dependency_audit"\ncapability = "dependency_audit"\n'
            'escalate = [{ signal = ["dependency-change"], level = "recommended" }]\n',
            "unknown escalate signal",
        ),
        (
            '[[verification]]\nid = "dependency_audit"\ncapability = "dependency_audit"\n'
            'escalate = [{ signal = "dependency-change", dimension = "security_privacy", min = 2, '
            'level = "required" }]\n',
            "either 'signal' or 'dimension'",
        ),
        ('[enforcement]\nhands_off = "false"\n', "hands_off must be true or false"),
        ('[auto]\napp_envelopes = "system-reminder"\n', "app_envelopes must be a list of non-empty"),
        ('[auto]\napp_envelopes = ["<system-reminder>"]\n', "app_envelopes must be tag names"),
        ('[auto]\napp_envelope_frames = [""]\n', "app_envelope_frames must be a list of non-empty"),
        ('[selection]\ndefault_package_manager = "pnmp"\n', "default_package_manager must be one of"),
    ],
)
def test_invalid_overrides_are_rejected(tmp_path: Path, snippet: str, message: str):
    write(tmp_path, ".susanoo/config.toml", "schema_version = 1\n" + snippet)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_invalid_toml_reports_path(tmp_path: Path):
    write(tmp_path, ".susanoo/config.toml", "this is = = not toml")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(tmp_path)
