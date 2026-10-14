"""Model-assisted routing: Claude routes in its own words, never below the user's message."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from susanoo.analyzer import Floor, assess
from susanoo.config import PROFILE_ORDER, Config, ConfigError, load_config
from susanoo.enforcement import handle
from susanoo.enforcement import prompt as prompt_state
from susanoo.enforcement.hooks import SessionState
from susanoo.evaluation.store import RecordStore
from susanoo.evaluation.suite import load_suite
from susanoo.paths import install_manifest_path, records_dir
from susanoo.pipeline import message_floor
from susanoo.router import Profile, route
from tests.conftest import write
from tests.test_cli import run

SESSION = "sess-agent-1"
RISK = ("low", "moderate", "high", "critical")


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, home: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    code, _, err = run(root, "init", "--enforce", "--enforce-scope", "settings", "--auto")
    assert code == 0, err
    return root


def _prompt(root: Path, prompt: str, mode: str = "default") -> str:
    event = {"session_id": SESSION, "cwd": str(root), "prompt": prompt, "permission_mode": mode}
    code, out = handle("user-prompt-submit", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _bash(root: Path, command: str, mode: str = "default") -> dict[str, str]:
    event = {
        "session_id": SESSION,
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "permission_mode": mode,
    }
    code, out = handle("pre-tool-use", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"] if out else {}


# --- the floor: never lower than the user's message ----------------------------------------


def test_merged_assessment_never_lowers_either_side(config: Config, profiles: dict[str, Profile]):
    """Every pair of suite tasks, one as the user's message and one as Claude's description."""
    tasks = [case["task"] for case in load_suite()[1]]
    for message in tasks:
        floor = message_floor(message, config)
        assert floor is not None
        alone = assess(message, config)
        floor_controls = {c.id for c in route(alone, profiles, config).controls}
        for description in tasks:
            own = assess(description, config)
            merged = assess(description, config, floor=floor)
            decision = route(merged, profiles, config, minimum=floor.profile)
            pair = f"{message!r} / {description!r}"
            assert PROFILE_ORDER.index(decision.profile) >= PROFILE_ORDER.index(floor.profile), pair
            for dim in merged.dimensions:
                assert merged.level(dim) >= max(alone.level(dim), own.level(dim)), (pair, dim)
            assert RISK.index(merged.risk.level) >= max(
                RISK.index(alone.risk.level), RISK.index(own.risk.level)
            ), pair
            assert floor_controls <= {c.id for c in decision.controls}, pair
            assert alone.signal_ids() <= merged.signal_ids(), pair
            for override in PROFILE_ORDER:  # an override changes the profile, never the controls
                forced = route(merged, profiles, config, override=override, minimum=floor.profile)
                assert floor_controls <= {c.id for c in forced.controls}, (pair, override)


def test_floor_keeps_what_the_description_left_out(config: Config, profiles: dict[str, Profile]):
    floor = message_floor("Drop the legacy_orders table", config)
    assert floor is not None and floor.profile == "deep"
    merged = assess("Clean up the order schema", config, floor=floor)
    decision = route(merged, profiles, config, minimum=floor.profile)
    assert decision.profile == "deep" and merged.risk.level == "critical"
    assert "destructive-data" in merged.signal_ids()
    assert any(r.capability == "migration_checks" for r in merged.requirements)
    assert any("the user's message" in r for r in merged.dimensions["data_integrity"].reasons)


def test_minimum_raises_the_recommendation_with_a_reason(config: Config, profiles: dict[str, Profile]):
    decision = route(assess("Fix typo in README", config), profiles, config, minimum="standard")
    assert decision.profile == decision.recommended == "standard"
    assert decision.reasons == ["the user's message alone routes STANDARD"]
    lower = route(assess("Add Stripe billing", config), profiles, config, minimum="fast")
    assert lower.profile == "deep"


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"levels": {}, "signals": [], "areas": []},
        {"levels": {"risk": "3"}, "signals": [], "areas": [], "profile": "deep"},
        {"levels": {"risk": 7}, "signals": [], "areas": [], "profile": "deep"},
        {"levels": {"risk": True}, "signals": [], "areas": [], "profile": "deep"},
        {"levels": {}, "signals": "payments", "areas": [], "profile": "deep"},
    ],
)
def test_malformed_floor_is_ignored(data: object):
    assert Floor.from_dict(data) is None


def test_named_profiles():
    assert prompt_state.named_profiles("/susanoo --profile fast fix the typo") == ["fast"]
    assert prompt_state.named_profiles("use the standard workflow, profile: deep") == ["standard", "deep"]
    assert prompt_state.named_profiles("don't use the fast workflow, not the deep profile") == []
    assert prompt_state.named_profiles("make the build fast") == []
    assert prompt_state.named_profiles("add a fast route for /health") == []


def test_vague_messages_do_not_raise_the_floor(config: Config):
    for message in ("ok", "thanks!", "What does app.py do?", "Can you look at the footer?"):
        floor = message_floor(message, config)
        assert floor is not None and floor.profile == "fast", message


def test_floor_reads_at_most_a_bounded_prefix(config: Config):
    floor = message_floor("x " * 20_000 + " drop the users table", config)
    assert floor is not None and "destructive-data" not in floor.signals


def test_corrupt_prompt_record_is_ignored(tmp_path: Path):
    root = _project(tmp_path)
    path = prompt_state.session_file(root, SESSION, ".prompt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe{not json")
    assert prompt_state.load(root, SESSION) is None


# --- the hook --------------------------------------------------------------------------------


def test_prompt_record_keeps_the_floor_but_not_the_text(tmp_path: Path):
    root = _project(tmp_path)
    note = _prompt(root, "The checkout page is broken when the cart is empty, sk-secret-words")
    assert note.startswith("Susanoo did not route this message")
    path = prompt_state.session_file(root, SESSION, ".prompt")
    raw = path.read_text(encoding="utf-8")
    assert "checkout" not in raw and "secret" not in raw
    data = json.loads(raw)
    assert data["kind"] == "ambiguous" and data["named"] == [] and data["hands_off"] is False
    floor = Floor.from_dict(data["floor"])
    assert floor is not None and "payments" in floor.signals
    assert RecordStore(root).read("routes") == []


def test_skip_prompts_are_recorded_without_a_note(tmp_path: Path):
    root = _project(tmp_path)
    assert _prompt(root, "/susanoo --profile fast add a column to users") == ""
    assert prompt_state.load(root, SESSION)["named"] == ["fast"]  # type: ignore[index]


def test_brief_offers_raising(tmp_path: Path):
    root = _project(tmp_path)
    assert "raise standard|deep" in _prompt(root, "Add pagination to the /users API endpoint")
    assert "raise standard|deep" in _prompt(root, "Add Stripe billing with refunds to the users endpoint")


# --- Claude routes an unrouted message ------------------------------------------------------


def test_agent_route_is_never_lower_than_the_message(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The checkout page is broken when the cart is empty")
    code, out, err = run(root, "route", "Fix the crash in the cart total when the cart is empty")
    assert code == 0, err
    assert "never lower than the user's message" in out and "DEEP" in out
    record = RecordStore(root).read("routes")[-1]
    assert record["routed_by"] == "agent" and record["auto"] is True and record["session_id"] == SESSION
    assert record["profile"] == "deep" and "payments" in record["signals"]
    assert Floor.from_dict(record["floor"]) is not None


DROP = "The nightly cleanup job that drops the legacy_orders table in production keeps failing"


@pytest.mark.parametrize("profile", ["deep", "standard", "fast"])
def test_profile_override_keeps_the_floor_controls(tmp_path: Path, profile: str):
    """`--profile` (even DEEP, even one the user named) never drops what the message requires."""
    root = _project(tmp_path)
    _prompt(root, f"{DROP}, use the {profile} workflow")
    code, _, err = run(root, "route", "--profile", profile, "Fix the retry logic in the cleanup script")
    assert code == 0, err
    record = RecordStore(root).read("routes")[-1]
    assert {"data-safety", "irreversible-action-gate"} <= set(record["controls"])
    assert {"database", "production"} <= set(record["signals"])
    assert record["risk"]["level"] in ("high", "critical")
    assert record["routed_by"] == "agent"


def test_replacing_a_stricter_route_keeps_its_levels(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Drop the legacy_orders table, work autonomously")
    store = RecordStore(root)
    deep = store.read("routes")[-1]
    assert deep["profile"] == "deep" and "irreversible-action-gate" in deep["controls"]
    _prompt(root, "also tidy the order model docstrings")
    code, out, err = run(root, "route", "Tidy the order model docstrings")
    assert code == 0, err
    assert f"replaces route {deep['id']}" in out
    new = store.read("routes")[-1]
    assert new["profile"] == "deep" and set(deep["controls"]) <= set(new["controls"])
    assert new["replaced"] == deep["id"]
    push = _bash(root, "psql -c 'DROP TABLE legacy_orders'")
    assert push.get("permissionDecision") == "ask"


def test_agent_route_replaces_an_idle_route_but_not_unfinished_work(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Fix typo in README")
    first = RecordStore(root).read("routes")[-1]["id"]
    _prompt(root, "Why is the footer misaligned on mobile?")
    code, _, err = run(root, "route", "Fix the footer alignment on mobile")
    assert code == 0, err
    store = RecordStore(root)
    assert [o["record_id"] for o in store.read("outcomes")] == [first]
    assert store.read("outcomes")[0]["source"] == "susanoo"
    second = store.read("routes")[-1]["id"]

    hook = SessionState(root, SESSION)
    hook.for_record(second)["work_started"] = True
    hook.save()
    _prompt(root, "Also the header looks off")
    code, _, err = run(root, "route", "Fix the header alignment")
    assert code != 0 and "unfinished work" in err
    assert store.read("routes")[-1]["id"] == second


def test_agent_route_follows_an_autonomy_request(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The settings page is broken, work autonomously")
    code, out, err = run(root, "route", "Fix the settings page form validation")
    assert code == 0, err
    assert "do not wait for approval" in out
    record = RecordStore(root).read("routes")[-1]
    assert record["autonomous"] is True
    assert [a["record_id"] for a in RecordStore(root).read("approvals")] == [record["id"]]


def test_skill_route_keeps_the_full_brief_with_the_floor(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "/susanoo drop the legacy_orders table")
    code, out, err = run(root, "route", "--stdin", stdin="Clean up the order schema")
    assert code == 0, err
    assert "# Susanoo execution brief" in out
    record = RecordStore(root).read("routes")[-1]
    assert "routed_by" not in record and "auto" not in record
    assert record["profile"] == "deep" and "destructive-data" in record["signals"]


def test_stale_prompt_record_is_ignored_once_auto_routing_is_off(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The checkout page is broken")
    manifest = install_manifest_path(root)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**data, "auto": False}), encoding="utf-8")
    code, out, _ = run(root, "route", "Fix typo in README")
    assert code == 0 and "# Susanoo execution brief" in out
    record = RecordStore(root).read("routes")[-1]
    assert record["profile"] == "fast" and "routed_by" not in record


def test_route_without_automatic_routing_is_unchanged(tmp_path: Path):
    root = tmp_path / "plain"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    assert run(root, "init")[0] == 0
    code, out, _ = run(root, "route", "Fix typo in README")
    assert code == 0 and "# Susanoo execution brief" in out
    assert "routed_by" not in RecordStore(root).read("routes")[-1]


def test_abbreviated_options_are_rejected(tmp_path: Path):
    root = _project(tmp_path)
    for option in ("--prof", "--pro=fast"):
        with pytest.raises(SystemExit):
            run(root, "route", option, *(["fast"] if "=" not in option else []), "fix it")


# --- raising a route ---------------------------------------------------------------------


def _approved_route(root: Path, prompt: str) -> dict[str, object]:
    _prompt(root, prompt)
    store = RecordStore(root)
    old = store.read("routes")[-1]
    store.append("approvals", {"record_id": old["id"], "checkpoint": "plan"})
    store.append(
        "evidence", {"record_id": old["id"], "step": "unit_tests", "status": "passed", "source": "executed"}
    )
    hook = SessionState(root, SESSION)
    hook.for_record(old["id"])["work_started"] = True
    hook.save()
    return old


def test_raise_keeps_evidence_and_hook_state(tmp_path: Path):
    root = _project(tmp_path)
    old = _approved_route(root, "Add pagination to the /users API endpoint")
    assert old["profile"] == "standard"
    code, out, err = run(root, "raise", "deep", "--reason", "the change is wider than it looks")
    assert code == 0, err
    assert "from STANDARD to DEEP" in out
    store = RecordStore(root)
    new = store.read("routes")[-1]
    assert new["profile"] == "deep" and new["raised_from"] == old["id"] and new["auto"] is True
    assert new["task"] == old["task"] and new["controls"] == old["controls"]
    assert [o["record_id"] for o in store.read("outcomes")] == [old["id"]]
    assert [e["record_id"] for e in store.read("evidence")] == [old["id"], new["id"]]
    assert SessionState(root, SESSION).data == {"record_id": new["id"], "work_started": True}


def test_raise_to_a_stricter_profile_needs_the_plan_approved_again(tmp_path: Path):
    """DEEP adds phases the user never approved; only autonomy given in words carries over."""
    root = _project(tmp_path)
    old = _approved_route(root, "Add pagination to the /users API endpoint")
    code, out, err = run(root, "raise", "deep", "--reason", "the change is wider than it looks")
    assert code == 0, err
    assert "approval again" in out
    assert [a["record_id"] for a in RecordStore(root).read("approvals")] == [old["id"]]


def test_raise_that_adds_controls_drops_the_plan_approval(tmp_path: Path):
    root = _project(tmp_path)
    old = _approved_route(root, "Redesign the plugin architecture across the api, ui, and worker modules")
    assert old["profile"] == "deep"
    code, out, err = run(root, "raise", "deep", "--reason", "it also touches the payments module")
    assert code == 0, err
    store = RecordStore(root)
    new = store.read("routes")[-1]
    assert new["profile"] == "deep" and set(old["controls"]) < set(new["controls"])
    assert [a["record_id"] for a in store.read("approvals")] == [old["id"]]
    assert "approval again" in out


def test_raise_rejects_no_record(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint")
    with pytest.raises(SystemExit):
        run(root, "raise", "deep", "--reason", "wider", "--no-record")


def test_raise_reason_adds_controls_and_asks_for_approval_again(tmp_path: Path):
    root = _project(tmp_path)
    old = _approved_route(root, "Add pagination to the /users API endpoint")
    reason = "it deletes all customer records in the production database"
    code, out, err = run(root, "raise", "deep", "--reason", reason)
    assert code == 0, err
    assert "approval again" in out
    store = RecordStore(root)
    new = store.read("routes")[-1]
    assert {"data-safety", "irreversible-action-gate"} <= set(new["controls"])
    assert [a["record_id"] for a in store.read("approvals")] == [old["id"]]
    assert _bash(root, "git push origin main").get("permissionDecision") == "ask"


def test_raise_keeps_everything_a_long_task_had(tmp_path: Path):
    """The stored task is truncated; the route's own levels still count."""
    root = _project(tmp_path)
    prompt = "Add a settings page to the dashboard. " + "It has many fields. " * 60
    prompt += "Also run the database migration in production."
    assert len(prompt) > 1000
    _prompt(root, prompt)
    old = RecordStore(root).read("routes")[-1]
    assert {"data-safety", "irreversible-action-gate"} <= set(old["controls"])
    code, _, err = run(root, "raise", "deep", "--reason", "it is bigger than it looks")
    assert code == 0, err
    new = RecordStore(root).read("routes")[-1]
    assert set(old["controls"]) <= set(new["controls"]) and set(old["signals"]) <= set(new["signals"])
    assert RISK.index(new["risk"]["level"]) >= RISK.index(old["risk"]["level"])


def test_raise_never_lowers(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Add Stripe billing with refunds to the users endpoint")
    routes = RecordStore(root).read("routes")
    code, out, _ = run(root, "raise", "standard", "--reason", "smaller than it looks")
    assert code == 0 and "never lowered" in out
    assert RecordStore(root).read("routes") == routes
    with pytest.raises(SystemExit):
        run(root, "raise", "fast", "--reason", "x")


def test_raise_needs_an_open_route(tmp_path: Path):
    root = _project(tmp_path)
    code, _, err = run(root, "raise", "deep", "--reason", "risky")
    assert code != 0 and "no open route" in err


# --- only the user picks a lighter workflow -----------------------------------------------


def _profile_route(root: Path, profile: str, task: str) -> tuple[int, str]:
    code, _, err = run(root, "route", "--profile", profile, task)
    return code, err


def test_claude_cannot_pick_a_lighter_profile_alone(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "ok go ahead")  # a light message: the description decides
    code, err = _profile_route(root, "fast", "Drop the legacy_orders table in production")
    assert code != 0 and "only the user can choose a lighter workflow than DEEP" in err
    assert RecordStore(root).read("routes") == []
    assert _profile_route(root, "deep", "Drop the legacy_orders table in production")[0] == 0


def test_a_profile_at_the_recommendation_is_not_lighter(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Users can't log in since the auth change")
    assert _profile_route(root, "standard", "Fix the login session check")[0] == 0


def test_open_route_counts_for_a_lighter_profile(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Add Stripe billing with refunds to the users endpoint")
    _prompt(root, "ok")
    code, err = _profile_route(root, "standard", "Tidy the billing docs")
    assert code != 0 and "lighter workflow than DEEP" in err


def test_a_profile_the_user_named_is_allowed(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "/susanoo --profile fast fix the login for users after the auth change")
    code, err = _profile_route(root, "fast", "fix the login for users after the auth change")
    assert code == 0, err
    record = RecordStore(root).read("routes")[-1]
    assert record["profile"] == "fast" and record["recommended"] == "standard"


def test_negated_profile_names_do_not_count():
    for message in (
        "avoid the fast workflow",
        "use deep instead of the fast workflow",
        "please don't ever use the fast workflow",
        "rather than the fast workflow use deep",
        "I don't think we should use the fast workflow",
        "you can't use the fast workflow",
        "we weren't going to use the fast workflow",
        "we werent going to use the fast workflow",
        "Please do NOT, under any circumstances, use the fast workflow",
    ):
        assert "fast" not in prompt_state.named_profiles(message), message
    assert prompt_state.named_profiles("I want the fast workflow") == ["fast"]
    assert prompt_state.named_profiles("Use the fast workflow. I don't need a plan.") == ["fast"]
    # A negation earlier in the same sentence counts against every later name (strict on purpose).
    assert prompt_state.named_profiles("not the deep profile, use the fast workflow") == []


def test_no_profile_check_without_automatic_routing(tmp_path: Path):
    root = tmp_path / "plain"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    assert run(root, "init")[0] == 0
    assert _profile_route(root, "fast", "Add Stripe billing")[0] == 0


# --- every message kind keeps the open route ------------------------------------------------


@pytest.mark.parametrize("message", ["/fix-issue 12", "!ls", "? why does the drop fail", "ok"])
def test_any_message_keeps_the_open_route_and_its_unfinished_work(tmp_path: Path, message: str):
    root = _project(tmp_path)
    _prompt(root, "Drop the legacy_orders table in production, work autonomously")
    store = RecordStore(root)
    deep = store.read("routes")[-1]
    _prompt(root, message)
    code, out, err = run(root, "route", "Tidy the footer docstrings")
    assert code == 0, err
    new = store.read("routes")[-1]
    assert new["profile"] == "deep" and set(deep["controls"]) <= set(new["controls"])
    assert [o["record_id"] for o in store.read("outcomes")] == [deep["id"]]

    hook = SessionState(root, SESSION)
    hook.for_record(new["id"])["work_started"] = True
    hook.save()
    _prompt(root, message)
    code, _, err = run(root, "route", "Tidy the header docstrings")
    assert code != 0 and "unfinished work" in err


def test_skill_route_replaces_an_open_route_with_its_levels(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Drop the legacy_orders table in production")
    deep = RecordStore(root).read("routes")[-1]
    _prompt(root, "/susanoo tidy the footer docstrings")
    code, out, err = run(root, "route", "--stdin", stdin="tidy the footer docstrings")
    assert code == 0, err
    assert "# Susanoo execution brief" in out and f"replaces route {deep['id']}" in out
    new = RecordStore(root).read("routes")[-1]
    assert new["profile"] == "deep" and set(deep["controls"]) <= set(new["controls"])
    assert "auto" not in new and new["replaced"] == deep["id"]


def test_agent_route_keeps_the_evidence_rules_of_a_skill_route(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "/susanoo Add Stripe billing with refunds")
    assert run(root, "route", "--stdin", stdin="Add Stripe billing with refunds")[0] == 0
    assert "auto" not in RecordStore(root).read("routes")[-1]
    _prompt(root, "ok")
    assert run(root, "route", "Add Stripe billing with refunds")[0] == 0
    new = RecordStore(root).read("routes")[-1]
    assert new["routed_by"] == "agent" and "auto" not in new


# --- raising: the target is a minimum ---------------------------------------------------------


def test_raise_target_is_a_minimum(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Fix typo in README")
    code, out, err = run(root, "raise", "standard", "--reason", "Drop the legacy_orders table in production")
    assert code == 0, err
    new = RecordStore(root).read("routes")[-1]
    assert new["profile"] == new["recommended"] == "deep" and new["overridden"] is False
    assert "to DEEP" in out


def test_raise_adds_controls_to_a_deep_route(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Redesign the plugin architecture across the api, ui, and worker modules")
    old = RecordStore(root).read("routes")[-1]
    assert old["profile"] == "deep" and "data-safety" not in old["controls"]
    reason = "it also deletes all customer records in the production database"
    code, out, err = run(root, "raise", "deep", "--reason", reason)
    assert code == 0, err
    new = RecordStore(root).read("routes")[-1]
    assert new["raised_from"] == old["id"] and "data-safety" in new["controls"]


def test_raise_with_autonomy_does_not_ask_for_approval_again(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Add pagination to the /users API endpoint, work autonomously")
    reason = "it deletes all customer records in the production database"
    code, out, err = run(root, "raise", "deep", "--reason", reason)
    assert code == 0, err
    assert "New safety controls" in out and "approval again" not in out
    assert "do not wait for approval" in out


# --- no way around the floors in a session ----------------------------------------------------


def _deep_route(root: Path) -> dict[str, object]:
    _prompt(root, "Drop the legacy_orders table in production, work autonomously")
    deep = RecordStore(root).read("routes")[-1]
    assert {"data-safety", "irreversible-action-gate"} <= set(deep["controls"])
    return deep


def test_extra_config_is_refused_in_a_session(tmp_path: Path):
    root = _project(tmp_path)
    _deep_route(root)
    _prompt(root, "ok")
    evil = write(tmp_path, "evil.toml", '[[controls]]\nid = "irreversible-action-gate"\nenabled = false\n')
    routes = RecordStore(root).read("routes")
    code, _, err = run(root, "route", "--config", str(evil), "Tidy the footer docstrings")
    assert code != 0 and "--config is not accepted" in err
    code, _, err = run(root, "raise", "deep", "--config", str(evil), "--reason", "wider")
    assert code != 0 and "--config is not accepted" in err
    assert RecordStore(root).read("routes") == routes


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_a_lost_prompt_record_keeps_the_open_route_floor(tmp_path: Path, damage: str):
    root = _project(tmp_path)
    deep = _deep_route(root)
    _prompt(root, "ok")
    path = prompt_state.session_file(root, SESSION, ".prompt")
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"\xff{")
    code, _, err = run(root, "route", "--profile", "fast", "Tidy the footer docstrings")
    assert code != 0 and "lighter workflow" in err
    code, _, err = run(root, "route", "Tidy the footer docstrings")
    assert code == 0, err
    new = RecordStore(root).read("routes")[-1]
    assert new["profile"] == "deep" and set(deep["controls"]) <= set(new["controls"])


# --- route before the first edit ----------------------------------------------------------------


def _edit(root: Path, path: Path | str, mode: str = "default") -> dict[str, str]:
    event = {
        "session_id": SESSION,
        "cwd": str(root),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path)},
        "permission_mode": mode,
    }
    code, out = handle("pre-tool-use", event)
    assert code == 0
    return json.loads(out)["hookSpecificOutput"] if out else {}


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "bypassPermissions"])
def test_first_edit_of_an_unrouted_message_is_denied_once(tmp_path: Path, mode: str):
    root = _project(tmp_path)
    _prompt(root, "app.py is missing the hello() function that should return the string 'hi'", mode)
    first = _edit(root, root / "app.py", mode)
    assert first["permissionDecision"] == "deny"
    assert "susanoo route" in first["permissionDecisionReason"]
    assert "ask the user" not in first["permissionDecisionReason"]
    assert _edit(root, root / "app.py", mode) == {}  # a retry goes through
    logged = RecordStore(root).read("enforcement")
    assert [e["permission"] for e in logged] == ["deny"]


def test_each_new_unrouted_message_gets_its_own_nudge(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The footer is misaligned")
    assert _edit(root, root / "app.py")["permissionDecision"] == "deny"
    _prompt(root, "The header is misaligned too")
    assert _edit(root, root / "app.py")["permissionDecision"] == "deny"


def test_no_nudge_once_claude_routed(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The footer is misaligned on mobile", "bypassPermissions")
    assert run(root, "route", "Fix the footer alignment on mobile")[0] == 0
    assert _edit(root, root / "app.py", "bypassPermissions") == {}
    # The route is closed, and more edits follow for the same message: still no nudge.
    assert run(root, "outcome", "latest", "--status", "partial", "--source", "agent")[0] == 0
    assert _edit(root, root / "app.py", "bypassPermissions") == {}


@pytest.mark.parametrize("message", ["?add a hello function to app.py", "/fix-issue 12", "!ls"])
def test_no_nudge_for_messages_that_asked_for_no_routing(tmp_path: Path, message: str):
    root = _project(tmp_path)
    _prompt(root, message)
    assert _edit(root, root / "app.py") == {}


def test_no_nudge_for_edits_outside_the_project(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "What does app.py do?")
    assert _edit(root, tmp_path / "scratch" / "notes.md") == {}


def test_no_nudge_for_routed_tasks(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "Fix typo in README")  # routed by the hook (FAST)
    assert _edit(root, root / "README.md") == {}


def test_route_before_edit_can_be_switched_off(tmp_path: Path):
    root = _project(tmp_path)
    config = root / ".susanoo" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\n[auto]\nroute_before_edit = false\n", encoding="utf-8"
    )
    _prompt(root, "The footer is misaligned")
    assert _edit(root, root / "app.py") == {}


def test_no_nudge_without_automatic_routing(tmp_path: Path):
    root = tmp_path / "plain"
    write(root, "app.py", "x = 1\n")
    (root / ".git").mkdir()
    assert run(root, "init")[0] == 0
    _prompt(root, "The footer is misaligned")
    assert _edit(root, root / "app.py") == {}


def test_a_failed_nudge_mark_fails_open_and_keeps_the_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _project(tmp_path)
    _prompt(root, "The payments refund flow drops production customer data, it is broken")
    before = prompt_state.load(root, SESSION)
    real_replace = os.replace

    def failing_replace(src: object, dst: object) -> None:
        if str(dst).endswith(".nudged.json"):
            raise OSError(28, "No space left on device")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", failing_replace)
    for _ in range(3):  # never a loop of denials
        assert _edit(root, root / "app.py") == {}
    assert prompt_state.load(root, SESSION) == before  # the floor record is untouched
    assert not list((records_dir(root) / "hooks").glob("*.tmp"))


def test_the_nudge_never_rewrites_the_prompt_record(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The footer is misaligned")
    path = prompt_state.session_file(root, SESSION, ".prompt")
    before = path.read_bytes()
    assert _edit(root, root / "app.py")["permissionDecision"] == "deny"
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["CLAUDE.md", "docs/CLAUDE.md", "AGENTS.md", ".claude/settings.local.json"])
def test_agent_memory_and_settings_are_exempt(tmp_path: Path, name: str):
    root = _project(tmp_path)
    _prompt(root, "remember that we use pytest here")
    assert _edit(root, name) == {}
    assert _edit(root, root / name) == {}


def test_a_route_from_before_the_message_does_not_count(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The footer is misaligned on mobile")
    assert run(root, "route", "Fix the footer alignment on mobile")[0] == 0
    assert run(root, "outcome", "latest", "--status", "partial", "--source", "agent")[0] == 0
    time.sleep(1.1)  # records are stamped to the second
    _prompt(root, "The header is misaligned too")
    assert _edit(root, root / "app.py")["permissionDecision"] == "deny"


def test_a_route_from_another_session_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _project(tmp_path)
    _prompt(root, "The footer is misaligned on mobile")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "another-session")
    assert run(root, "route", "--session", "another-session", "Fix the footer")[0] == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    assert _edit(root, root / "app.py")["permissionDecision"] == "deny"


def test_no_nudge_once_automatic_routing_is_turned_off(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The footer is misaligned")
    manifest = install_manifest_path(root)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps({**data, "auto": False}), encoding="utf-8")
    assert _edit(root, root / "app.py") == {}


def test_notebooks_are_covered_and_a_missing_path_is_not(tmp_path: Path):
    root = _project(tmp_path)
    _prompt(root, "The analysis notebook gives wrong totals")
    notebook = {
        "session_id": SESSION,
        "cwd": str(root),
        "tool_name": "NotebookEdit",
        "tool_input": {"notebook_path": str(root / "analysis.ipynb")},
        "permission_mode": "default",
    }
    no_path = {**notebook, "tool_name": "Edit", "tool_input": {}}
    assert handle("pre-tool-use", no_path)[1] == ""
    out = json.loads(handle("pre-tool-use", notebook)[1])["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


def test_route_before_edit_settings_are_validated(tmp_path: Path):
    root = _project(tmp_path)
    config = root / ".susanoo" / "config.toml"
    original = config.read_text(encoding="utf-8")
    for bad in ('route_before_edit = "true"', 'route_before_edit_exempt = "CLAUDE.md"'):
        config.write_text(original + f"\n[auto]\n{bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(root)
