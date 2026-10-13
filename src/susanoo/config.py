"""Configuration loading: packaged defaults deep-merged with project overrides."""

from __future__ import annotations

import copy
import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from susanoo.paths import project_config_path

DIMENSIONS: tuple[str, ...] = (
    "implementation_complexity",
    "architectural_complexity",
    "affected_components",
    "technical_uncertainty",
    "external_dependencies",
    "security_privacy",
    "data_integrity",
    "irreversibility",
    "testing_difficulty",
    "parallelization",
)

PROFILE_ORDER: tuple[str, ...] = ("fast", "standard", "deep")
REQUIREMENT_LEVELS: tuple[str, ...] = ("recommended", "required")

# Arrays of tables merged by their `id` key instead of being replaced wholesale.
_ID_MERGED_ARRAYS = frozenset({"signals", "controls", "verification"})


class ConfigError(Exception):
    """Raised when configuration is missing, unreadable, or invalid."""


Config = dict[str, Any]


def read_data_text(*parts: str) -> str:
    """Read a text file shipped in the ``susanoo.data`` package directory."""
    node = resources.files("susanoo").joinpath("data", *parts)
    return node.read_text(encoding="utf-8")


def load_toml_data(*parts: str) -> dict[str, Any]:
    return tomllib.loads(read_data_text(*parts))


def load_toml_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` into a copy of ``base``.

    Nested tables merge recursively. Arrays named in ``_ID_MERGED_ARRAYS`` merge
    entry-by-entry on ``id``; an override entry with ``enabled = false`` removes
    the base entry. All other values (including other arrays) are replaced.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = merge(current, value)
        elif key in _ID_MERGED_ARRAYS and isinstance(current, list) and isinstance(value, list):
            result[key] = _merge_by_id(current, value, key)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _merge_by_id(base: list[Any], override: list[Any], name: str) -> list[Any]:
    merged: dict[str, dict[str, Any]] = {}
    for entry in base:
        merged[entry["id"]] = copy.deepcopy(entry)
    for entry in override:
        if not isinstance(entry, dict) or "id" not in entry:
            raise ConfigError(f"every entry in [[{name}]] overrides needs an 'id'")
        entry_id = entry["id"]
        if entry.get("enabled") is False:
            merged.pop(entry_id, None)
            continue
        merged[entry_id] = merge(merged.get(entry_id, {}), entry)
    return list(merged.values())


def load_config(root: Path | None = None, extra: Path | None = None) -> Config:
    """Load defaults, then ``<root>/.susanoo/config.toml``, then ``extra`` if given."""
    config = load_toml_data("defaults.toml")
    sources = ["defaults"]
    if root is not None:
        project_file = project_config_path(root)
        if project_file.is_file():
            config = merge(config, load_toml_file(project_file))
            sources.append(str(project_file))
    if extra is not None:
        config = merge(config, load_toml_file(extra))
        sources.append(str(extra))
    validate(config)
    config["_sources"] = sources
    return config


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _check_thresholds(section: str, thresholds: Any, names: tuple[str, ...]) -> None:
    _require(isinstance(thresholds, dict), f"[{section}] thresholds must be a table")
    values = []
    for name in names:
        value = thresholds.get(name)
        _require(
            isinstance(value, int | float) and 0.0 <= value <= 1.0,
            f"[{section}] thresholds.{name} must be a number between 0 and 1",
        )
        values.append(float(value))
    _require(values == sorted(values), f"[{section}] thresholds must be increasing: {names}")


def _check_level(where: str, value: Any) -> None:
    _require(
        isinstance(value, int) and 0 <= value <= 3,
        f"{where}: dimension levels must be integers between 0 and 3",
    )


def _check_dimension_map(where: str, mapping: Any) -> None:
    _require(isinstance(mapping, dict), f"{where} must be a table")
    for dim, level in mapping.items():
        _require(dim in DIMENSIONS, f"{where}: unknown dimension '{dim}'")
        _check_level(f"{where}.{dim}", level)


def _check_patterns(where: str, patterns: Any) -> None:
    _require(
        isinstance(patterns, list) and all(isinstance(p, str) for p in patterns),
        f"{where} must be a list of strings",
    )
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{where}: invalid regular expression {pattern!r} ({exc})") from exc


def validate(config: Config) -> None:
    """Validate the merged configuration, raising ``ConfigError`` with a precise message."""
    _require(config.get("schema_version") == 1, "unsupported or missing schema_version (expected 1)")

    weights = config.get("complexity", {}).get("weights", {})
    _require(bool(weights), "[complexity] weights must not be empty")
    for dim, weight in weights.items():
        _require(dim in DIMENSIONS, f"[complexity] weights: unknown dimension '{dim}'")
        _require(isinstance(weight, int | float) and weight >= 0, f"[complexity] weight '{dim}' must be >= 0")
    _require(sum(weights.values()) > 0, "[complexity] weights must sum to more than 0")
    _check_thresholds("complexity", config["complexity"].get("thresholds"), ("moderate", "high"))

    risk = config.get("risk", {})
    for dim in risk.get("dimensions", []):
        _require(dim in DIMENSIONS, f"[risk] dimensions: unknown dimension '{dim}'")
    _require(bool(risk.get("dimensions")), "[risk] dimensions must not be empty")
    max_weight = risk.get("max_weight")
    _require(
        isinstance(max_weight, int | float) and 0.0 <= max_weight <= 1.0,
        "[risk] max_weight must be between 0 and 1",
    )
    _check_thresholds("risk", risk.get("thresholds"), ("moderate", "high", "critical"))
    _check_thresholds("uncertainty", config.get("uncertainty", {}).get("thresholds"), ("moderate", "high"))

    floor = config.get("routing", {}).get("critical_risk_floor", "fast")
    _require(floor in PROFILE_ORDER, f"[routing] critical_risk_floor must be one of {PROFILE_ORDER}")

    _check_dimension_map("[assessment] baseline", config.get("assessment", {}).get("baseline", {}))

    seen: set[str] = set()
    for signal in config.get("signals", []):
        sid = signal.get("id")
        _require(isinstance(sid, str) and bool(sid), "every [[signals]] entry needs a string 'id'")
        _require(sid not in seen, f"duplicate signal id '{sid}'")
        seen.add(sid)
        patterns = signal.get("patterns")
        _require(
            isinstance(patterns, list) and bool(patterns) and all(isinstance(p, str) for p in patterns),
            f"signal '{sid}': 'patterns' must be a non-empty list of strings",
        )
        _check_dimension_map(f"signal '{sid}' effects", signal.get("effects", {}))
        _check_dimension_map(f"signal '{sid}' caps", signal.get("caps", {}))
        _check_patterns(f"signal '{sid}' caps_only_if", signal.get("caps_only_if", []))

    negation = config.get("assessment", {}).get("negation", {})
    for key in ("patterns", "locations"):
        _check_patterns(f"[assessment.negation] {key}", negation.get(key, []))
    filler = negation.get("filler", 3)
    _require(
        isinstance(filler, int) and not isinstance(filler, bool) and 0 <= filler <= 6,
        "[assessment.negation] filler must be an integer from 0 to 6",
    )
    id_lists = {
        f"[assessment.negation] {key}": negation.get(key, [])
        for key in ("keep_signals", "requires_signals", "blocking_signals")
    }
    clause = negation.get("clause", "")
    _require(isinstance(clause, str), "[assessment.negation] clause must be a string")
    _check_patterns("[assessment.negation] clause", [clause] if clause else [])
    for signal in config.get("signals", []):
        id_lists[f"signal '{signal['id']}' caps_unless_signals"] = signal.get("caps_unless_signals", [])
    for where, ids in id_lists.items():
        _require(
            isinstance(ids, list) and all(isinstance(i, str) for i in ids), f"{where} must be a list of ids"
        )
        for sid in ids:
            _require(sid in seen, f"{where}: unknown signal '{sid}'")
    # Patterns are complete only once {target} and {location} are expanded.
    from susanoo.analyzer.engine import negation_patterns  # imported here: the engine imports config

    _check_patterns("[assessment.negation] patterns (expanded)", negation_patterns(negation))

    for control in config.get("controls", []):
        cid = control.get("id", "?")
        when = control.get("when", {})
        _require(when.get("dimension") in DIMENSIONS, f"control '{cid}': unknown dimension in 'when'")
        _check_level(f"control '{cid}' when.min", when.get("min"))

    for rule in config.get("verification", []):
        rid = rule.get("id", "?")
        _require(isinstance(rule.get("capability"), str), f"verification '{rid}': missing 'capability'")
        for profile, level in rule.get("levels", {}).items():
            _require(profile in PROFILE_ORDER, f"verification '{rid}': unknown profile '{profile}'")
            _require(level in REQUIREMENT_LEVELS, f"verification '{rid}': unknown level '{level}'")
        for esc in rule.get("escalate", []):
            _require(esc.get("dimension") in DIMENSIONS, f"verification '{rid}': unknown escalate dimension")
            _check_level(f"verification '{rid}' escalate.min", esc.get("min"))
            _require(esc.get("level") in REQUIREMENT_LEVELS, f"verification '{rid}': unknown escalate level")
