"""Routes recorded in one checkout of a git repository, verified from another (a git worktree)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess  # nosec B404 - tests drive a real `git worktree` when git is installed
from pathlib import Path

import pytest

import susanoo.cli as cli
from susanoo.cli import main
from susanoo.enforcement.hooks import SessionState, handle
from susanoo.enforcement.state import active_route, evidence_status
from susanoo.evaluation.store import RecordStore
from susanoo.paths import global_manifest_path, head_commit, sibling_checkouts
from susanoo.verification.executor import Evidence
from tests.conftest import GATED_TOML

SESSION = "session-wt"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([args[0], "--path", str(root), *args[1:]], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def link_worktree(main_root: Path, checkout: Path, name: str = "wt") -> Path:
    """Lay out ``checkout`` as a linked worktree of ``main_root`` the way `git worktree add` does."""
    entry = main_root / ".git" / "worktrees" / name
    entry.mkdir(parents=True)
    (entry / "gitdir").write_text(str(checkout / ".git") + "\n", encoding="utf-8")
    (entry / "commondir").write_text("../..\n", encoding="utf-8")
    (entry / "HEAD").write_text(f"ref: refs/heads/{name}\n", encoding="utf-8")
    ref = main_root / ".git" / "refs" / "heads" / name
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(COMMIT + "\n", encoding="utf-8")
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / ".git").write_text(f"gitdir: {entry}\n", encoding="utf-8")
    return checkout


@pytest.fixture
def repo(python_project: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout and a linked worktree with the same files, both in project mode."""
    worktree = tmp_path / "pyproj-wt"
    shutil.copytree(python_project, worktree, ignore=shutil.ignore_patterns(".git"))
    link_worktree(python_project, worktree)
    for root in (python_project, worktree):
        (root / ".susanoo").mkdir()
    return python_project, worktree


@pytest.fixture
def ran_in(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace command execution: every planned step passes, and the folder it ran in is kept."""
    roots: list[Path] = []

    def fake_execute(plan, root, **_kwargs):  # type: ignore[no-untyped-def]
        roots.append(Path(root))
        return [
            Evidence(s.id, s.capability, "passed", "executed", ["true"], 0, 0.1, "2026-10-15T00:00:00+00:00")
            for s in plan.steps
        ]

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    return roots


def route_in(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    session: str | None = SESSION,
    task: str = "Refactor the parser module and add unit tests",
) -> str:
    if session:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    else:
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    code, _, err = run(root, "route", task)
    assert code == 0, err
    return str(RecordStore(root).read("routes")[-1]["id"])


# --- finding the repository's checkouts -------------------------------------------------


def test_checkouts_of_a_repository_see_each_other(repo: tuple[Path, Path]):
    main_root, worktree = repo
    assert [p.resolve() for p in sibling_checkouts(worktree)] == [main_root.resolve()]
    assert [p.resolve() for p in sibling_checkouts(main_root)] == [worktree.resolve()]


def test_no_checkouts_outside_git_or_without_worktrees(python_project: Path, tmp_path: Path):
    assert sibling_checkouts(tmp_path) == []
    assert sibling_checkouts(python_project) == []


def test_stale_or_foreign_worktree_entries_are_ignored(python_project: Path, tmp_path: Path):
    # A removed worktree whose entry git has not pruned yet.
    link_worktree(python_project, tmp_path / "gone", "gone")
    shutil.rmtree(tmp_path / "gone")
    # An entry edited to point at a folder that belongs to another repository.
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    entry = python_project / ".git" / "worktrees" / "foreign"
    entry.mkdir(parents=True)
    (entry / "gitdir").write_text(str(other / ".git"), encoding="utf-8")
    assert sibling_checkouts(python_project) == []


def test_an_unregistered_folder_pointing_at_the_repository_is_not_a_checkout(
    repo: tuple[Path, Path], tmp_path: Path
):
    main_root, _ = repo
    rogue = tmp_path / "rogue"
    rogue.mkdir()
    (rogue / ".git").write_text(f"gitdir: {main_root / '.git'}\n", encoding="utf-8")
    assert sibling_checkouts(rogue) == []


def test_corrupt_git_files_are_ignored(repo: tuple[Path, Path], tmp_path: Path):
    main_root, worktree = repo
    entry = main_root / ".git" / "worktrees" / "corrupt"
    entry.mkdir()
    (entry / "gitdir").write_text("C:/some" + chr(0) + "where/.git", encoding="utf-8")
    assert [p.resolve() for p in sibling_checkouts(main_root)] == [worktree.resolve()]
    (main_root / ".git" / "HEAD").write_text("ref: refs/heads/a" + chr(0) + "b", encoding="utf-8")
    assert head_commit(main_root) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_worktree(tmp_path: Path):
    main_root, worktree = tmp_path / "main", tmp_path / "linked"
    main_root.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=main_root, check=True)  # nosec B603
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"], cwd=main_root, check=True)  # nosec B603
    subprocess.run([*git, "worktree", "add", "-q", str(worktree)], cwd=main_root, check=True)  # nosec B603
    assert [p.resolve() for p in sibling_checkouts(worktree)] == [main_root.resolve()]
    assert [p.resolve() for p in sibling_checkouts(main_root)] == [worktree.resolve()]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "work"], cwd=worktree, check=True)  # nosec B603
    subprocess.run([*git, "pack-refs", "--all"], cwd=main_root, check=True)  # nosec B603
    for checkout in (main_root, worktree):
        head = subprocess.run(  # nosec B603
            [*git, "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True
        ).stdout.strip()
        assert head_commit(checkout) == head
    assert head_commit(main_root) != head_commit(worktree)


# --- the commit a checkout has checked out ------------------------------------------------


def test_head_commit_follows_a_worktrees_branch_to_the_common_refs(repo: tuple[Path, Path]):
    main_root, worktree = repo
    assert head_commit(worktree) == COMMIT
    assert head_commit(main_root) is None  # no HEAD written in the fixture's main `.git`


def test_head_commit_reads_detached_heads_and_packed_refs(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(COMMIT + "\n", encoding="utf-8")
    assert head_commit(tmp_path) == COMMIT
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert head_commit(tmp_path) is None
    (git_dir / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{'f' * 40} refs/heads/other\n{COMMIT} refs/heads/main\n",
        encoding="utf-8",
    )
    assert head_commit(tmp_path) == COMMIT


@pytest.mark.parametrize(
    "head",
    [
        "ref: ../../outside",
        "ref: refs/../../outside",
        r"ref: refs/heads\..\..\..\outside",
        "ref: refs/heads/C:/outside",
        "not a commit",
        "",
    ],
)
def test_head_commit_refuses_odd_heads(tmp_path: Path, head: str):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (tmp_path / "outside").write_text(COMMIT, encoding="utf-8")
    (git_dir / "HEAD").write_text(head, encoding="utf-8")
    assert head_commit(tmp_path) is None
    assert head_commit(tmp_path / "no-repo") is None


# --- verify, evidence, and outcome across checkouts --------------------------------------


def test_verify_in_worktree_records_executed_evidence_on_main_route(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    record_id = route_in(main_root, monkeypatch)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    code, out, err = run(worktree, "verify", "--execute", "--record", record_id)
    assert code == 0, err
    # The checks ran on the worktree's code ...
    assert ran_in == [worktree.resolve()]
    # ... and their evidence sits next to the route, saying where it ran.
    evidence = RecordStore(main_root).read("evidence")
    assert evidence and all(e["record_id"] == record_id and e["source"] == "executed" for e in evidence)
    assert {e["checkout"] for e in evidence} == {str(worktree.resolve())}
    assert {e["commit"] for e in evidence} == {COMMIT}
    assert RecordStore(worktree).read("evidence") == []
    assert str(main_root.resolve()) in out
    # The evidence gate accepts it.
    code, _, err = run(worktree, "outcome", record_id, "--status", "completed", "--source", "agent")
    assert code == 0, err
    assert RecordStore(main_root).read("outcomes")[-1]["status"] == "completed"


def test_evidence_in_the_routes_own_checkout_has_no_checkout_field(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, _ = repo
    record_id = route_in(main_root, monkeypatch)
    assert run(main_root, "verify", "--execute", "--record", record_id)[0] == 0
    assert all("checkout" not in e for e in RecordStore(main_root).read("evidence"))


def test_latest_finds_the_sessions_route_in_another_checkout(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    record_id = route_in(main_root, monkeypatch)
    code, _, err = run(worktree, "verify", "--execute")
    assert code == 0, err
    assert {e["record_id"] for e in RecordStore(main_root).read("evidence")} == {record_id}
    # The Stop hook reads the main checkout, where the session's route is: the executed evidence
    # from the worktree leaves nothing missing, so it no longer blocks the turn end.
    state = active_route(RecordStore(main_root), SESSION)
    assert state is not None and state.missing == [] and state.failing == []
    code, _, err = run(worktree, "evidence", "latest", "--step", "independent_review", "--status", "passed")
    assert code == 0, err
    reported = RecordStore(main_root).read("evidence")[-1]
    assert reported["source"] == "reported" and reported["checkout"] == str(worktree.resolve())
    code, _, err = run(worktree, "outcome", "latest", "--status", "completed", "--source", "agent")
    assert code == 0, err
    assert active_route(RecordStore(main_root), SESSION) is None


def test_latest_prefers_the_sessions_route_here(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    route_in(main_root, monkeypatch)
    here = route_in(worktree, monkeypatch)
    assert run(worktree, "verify", "--execute")[0] == 0
    assert {e["record_id"] for e in RecordStore(worktree).read("evidence")} == {here}
    assert RecordStore(main_root).read("evidence") == []


def test_latest_without_a_session_does_not_guess_across_checkouts(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    route_in(main_root, monkeypatch, session=None)
    code, _, err = run(worktree, "verify", "--execute")
    assert code == 1
    assert "no route records" in err and str(main_root.resolve()) in err
    assert ran_in == [] and RecordStore(main_root).read("evidence") == []


def test_other_sessions_routes_are_not_taken_as_latest(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    route_in(main_root, monkeypatch, session="someone-else")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    assert run(worktree, "verify", "--execute")[0] == 1
    assert RecordStore(main_root).read("evidence") == []


def test_routes_of_another_repository_are_never_found(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _, worktree = repo
    other = tmp_path / "unrelated"
    (other / ".git").mkdir(parents=True)
    (other / ".susanoo").mkdir()
    record_id = route_in(other, monkeypatch)
    code, _, err = run(worktree, "verify", "--execute", "--record", record_id)
    assert code == 1 and "no route record" in err
    assert ran_in == []


def test_global_mode_worktree_evidence_goes_to_the_main_checkouts_records(
    python_project: Path, tmp_path: Path, ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    path = global_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"global": True}), encoding="utf-8")
    worktree = tmp_path / "pyproj-wt"
    shutil.copytree(python_project, worktree, ignore=shutil.ignore_patterns(".git"))
    link_worktree(python_project, worktree)
    record_id = route_in(python_project, monkeypatch)
    assert RecordStore(worktree).directory != RecordStore(python_project).directory
    code, _, err = run(worktree, "verify", "--execute", "--record", record_id[:12])
    assert code == 0, err
    evidence = RecordStore(python_project).read("evidence")
    assert evidence and {e["checkout"] for e in evidence} == {str(worktree.resolve())}


def test_a_prefix_matching_routes_in_several_checkouts_is_refused(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    main_root, worktree = repo
    second = tmp_path / "pyproj-wt2"
    shutil.copytree(worktree, second, ignore=shutil.ignore_patterns(".git", ".susanoo"))
    link_worktree(main_root, second, "wt2")
    (second / ".susanoo").mkdir()
    route_in(worktree, monkeypatch)
    route_in(second, monkeypatch)
    code, _, err = run(main_root, "verify", "--execute", "--record", "su-")
    assert code == 1 and "matches 2 records" in err
    assert ran_in == []


def test_unregistered_folder_cannot_record_evidence_on_a_route(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    main_root, _ = repo
    record_id = route_in(main_root, monkeypatch)
    rogue = tmp_path / "rogue"
    shutil.copytree(main_root, rogue, ignore=shutil.ignore_patterns(".git", ".susanoo"))
    (rogue / ".git").write_text(f"gitdir: {main_root / '.git'}\n", encoding="utf-8")
    code, _, err = run(rogue, "verify", "--execute", "--record", record_id)
    assert code == 1 and "no route record" in err
    assert ran_in == [] and RecordStore(main_root).read("evidence") == []


def test_latest_takes_the_sessions_newest_route_in_any_checkout(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    older = route_in(worktree, monkeypatch)
    path = RecordStore(worktree).directory / "routes.jsonl"
    route = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**route, "created_at": "2000-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    newer = route_in(main_root, monkeypatch)
    assert run(worktree, "verify", "--execute")[0] == 0
    assert {e["record_id"] for e in RecordStore(main_root).read("evidence")} == {newer}
    assert all(e["record_id"] != older for e in RecordStore(worktree).read("evidence"))


def _executed(step: str, status: str, checkout: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"record_id": "r1", "step": step, "status": status, "source": "executed"}
    return {**item, "checkout": checkout} if checkout else item


def test_a_pass_in_one_checkout_does_not_clear_a_failure_in_another():
    route = {"id": "r1", "verification": [{"id": "unit_tests", "level": "required", "mode": "ready"}]}
    failed_here = [_executed("unit_tests", "failed", "/wt1"), _executed("unit_tests", "passed", "/wt2")]
    assert evidence_status(route, failed_here) == ([], ["unit_tests"])
    assert evidence_status(
        route, [_executed("unit_tests", "failed"), _executed("unit_tests", "passed", "/wt2")]
    ) == (
        [],
        ["unit_tests"],
    )
    fixed = [*failed_here, _executed("unit_tests", "passed", "/wt1")]
    assert evidence_status(route, fixed) == ([], [])
    # Within one checkout the latest run still counts, as before.
    assert evidence_status(route, [_executed("unit_tests", "failed"), _executed("unit_tests", "passed")]) == (
        [],
        [],
    )


def test_outcome_is_refused_while_the_worktree_that_failed_has_not_passed(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    main_root, worktree = repo
    clean = tmp_path / "pyproj-wt2"
    shutil.copytree(worktree, clean, ignore=shutil.ignore_patterns(".git"))
    link_worktree(main_root, clean, "wt2")
    status = {"value": "failed"}

    def fake_execute(plan, root, **_kwargs):  # type: ignore[no-untyped-def]
        return [
            Evidence(
                s.id, s.capability, status["value"], "executed", ["true"], 0, 0.1, "2026-10-15T00:00:00+00:00"
            )
            for s in plan.steps
            if s.mode == "ready"
        ]

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    record_id = route_in(main_root, monkeypatch)
    assert run(worktree, "verify", "--execute")[0] == 1
    status["value"] = "passed"
    assert run(clean, "verify", "--execute")[0] == 0
    code, _, err = run(worktree, "outcome", "latest", "--status", "completed", "--source", "agent")
    assert code == 1 and "lacks passing evidence" in err and "unit_tests" in err
    assert run(worktree, "verify", "--execute")[0] == 0
    assert run(worktree, "evidence", "latest", "--step", "independent_review", "--status", "passed")[0] == 0
    code, _, err = run(worktree, "outcome", record_id, "--status", "completed", "--source", "agent")
    assert code == 0, err


def test_a_worktree_without_its_own_records_uses_the_main_checkouts(
    python_project: Path, tmp_path: Path, ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    (python_project / ".susanoo").mkdir()
    worktree = tmp_path / "pyproj-wt"
    shutil.copytree(python_project, worktree, ignore=shutil.ignore_patterns(".git", ".susanoo"))
    link_worktree(python_project, worktree)
    record_id = route_in(python_project, monkeypatch)
    assert run(worktree, "verify", "--execute")[0] == 0
    assert run(worktree, "evidence", "latest", "--step", "independent_review", "--status", "passed")[0] == 0
    code, _, err = run(worktree, "outcome", "latest", "--status", "completed", "--source", "agent")
    assert code == 0, err
    assert RecordStore(python_project).read("outcomes")[-1]["record_id"] == record_id
    # Lookup errors are reported as they are, not as a missing `susanoo init`.
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    code, _, err = run(worktree, "outcome", "latest", "--status", "completed", "--source", "agent")
    assert code == 1 and "other checkouts of this repository" in err and "susanoo init" not in err


# --- raising a route across checkouts ----------------------------------------------------

STANDARD_TASK = "Add pagination to the /users API endpoint"
WIDER = "the change is wider than it looks"


def stop_in(root: Path) -> str:
    """The Stop hook's verdict in ``root`` ("" when the turn may end)."""
    code, out = handle("stop", {"session_id": SESSION, "cwd": str(root)})
    assert code == 0
    return out


def test_raise_in_a_worktree_raises_the_main_checkouts_route_and_its_gate_enforces_it(
    repo: tuple[Path, Path], ran_in: list[Path], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    main_root, worktree = repo
    old = route_in(main_root, monkeypatch, task=STANDARD_TASK)
    assert RecordStore(main_root).read("routes")[-1]["profile"] == "standard"
    hook = SessionState(main_root, SESSION)
    hook.for_record(old)["work_started"] = True
    hook.save()
    store = RecordStore(main_root)
    store.append(
        "evidence", {"record_id": old, "step": "unit_tests", "status": "passed", "source": "executed"}
    )
    assert f"STANDARD, record {old}" in json.loads(stop_in(main_root))["reason"]

    code, out, err = run(worktree, "raise", "deep", "--reason", WIDER)
    assert code == 0, err
    assert "from STANDARD to DEEP" in out and str(main_root.resolve()) in out
    # The raised route replaces the old one where it was recorded, not in the worktree.
    new = store.read("routes")[-1]
    assert new["profile"] == "deep" and new["raised_from"] == old and new["session_id"] == SESSION
    assert RecordStore(worktree).read("routes") == []
    assert [(o["record_id"], o["status"]) for o in store.read("outcomes")] == [(old, "abandoned")]
    assert [e["record_id"] for e in store.read("evidence")] == [old, new["id"]]
    hook = SessionState(main_root, SESSION)
    assert hook.data["record_id"] == new["id"] and hook.data["work_started"] is True
    # The main checkout's Stop hook now enforces the DEEP route ...
    verdict = json.loads(stop_in(main_root))
    assert verdict["decision"] == "block" and f"DEEP, record {new['id']}" in verdict["reason"]
    # ... and checks run in the worktree count for it, so the gate releases.
    assert run(worktree, "verify", "--execute")[0] == 0
    assert stop_in(main_root) == ""


def test_raise_from_a_worktree_never_takes_another_sessions_route(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    route_in(main_root, monkeypatch, session="someone-else", task=STANDARD_TASK)
    routes = RecordStore(main_root).read("routes")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    code, _, err = run(worktree, "raise", "deep", "--reason", WIDER)
    assert code == 1 and "no open route" in err
    assert RecordStore(main_root).read("routes") == routes and RecordStore(worktree).read("routes") == []


def test_raise_from_a_worktree_never_lowers(repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    main_root, worktree = repo
    route_in(main_root, monkeypatch, task=STANDARD_TASK)
    assert run(worktree, "raise", "deep", "--reason", WIDER)[0] == 0
    routes = RecordStore(main_root).read("routes")
    code, out, _ = run(worktree, "raise", "standard", "--reason", "smaller than it looks")
    assert code == 0 and "never lowered" in out
    assert RecordStore(main_root).read("routes") == routes


def test_raise_takes_the_sessions_newest_open_route_in_any_checkout(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    main_root, worktree = repo
    older = route_in(worktree, monkeypatch, task=STANDARD_TASK)
    path = RecordStore(worktree).directory / "routes.jsonl"
    route = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**route, "created_at": "2000-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    newer = route_in(main_root, monkeypatch, task=STANDARD_TASK)
    assert run(worktree, "raise", "deep", "--reason", WIDER)[0] == 0
    assert RecordStore(main_root).read("routes")[-1]["raised_from"] == newer
    assert [r["id"] for r in RecordStore(worktree).read("routes")] == [older]
    # A closed route is not open: with the main checkout's route finished, the worktree's is next.
    raised = str(RecordStore(main_root).read("routes")[-1]["id"])
    assert run(main_root, "outcome", raised, "--status", "abandoned", "--source", "agent")[0] == 0
    assert run(worktree, "raise", "deep", "--reason", WIDER)[0] == 0
    assert RecordStore(worktree).read("routes")[-1]["raised_from"] == older
    assert RecordStore(main_root).read("routes")[-1]["id"] == raised


def test_an_unregistered_folder_cannot_raise_a_route(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    main_root, _ = repo
    route_in(main_root, monkeypatch, task=STANDARD_TASK)
    routes = RecordStore(main_root).read("routes")
    rogue = tmp_path / "rogue"
    shutil.copytree(main_root, rogue, ignore=shutil.ignore_patterns(".git", ".susanoo"))
    (rogue / ".git").write_text(f"gitdir: {main_root / '.git'}\n", encoding="utf-8")
    code, _, err = run(rogue, "raise", "deep", "--reason", WIDER)
    assert code == 1 and "susanoo init" in err
    (rogue / ".susanoo").mkdir()
    code, _, err = run(rogue, "raise", "deep", "--reason", WIDER)
    assert code == 1 and "no open route" in err
    assert RecordStore(main_root).read("routes") == routes


def test_raise_from_a_worktree_without_records_keeps_the_automatic_routes_rules(
    python_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    code, _, err = run(python_project, "init", "--enforce", "--enforce-scope", "settings", "--auto")
    assert code == 0, err
    with (python_project / ".susanoo" / "config.toml").open("a", encoding="utf-8") as fh:
        fh.write(GATED_TOML)  # only the main checkout asks for approvals
    worktree = tmp_path / "pyproj-wt"
    shutil.copytree(python_project, worktree, ignore=shutil.ignore_patterns(".git", ".susanoo", ".claude"))
    link_worktree(python_project, worktree)
    event = {"session_id": SESSION, "cwd": str(python_project), "prompt": STANDARD_TASK}
    assert handle("user-prompt-submit", event)[0] == 0
    old = RecordStore(python_project).read("routes")[-1]
    assert old["auto"] is True and old["profile"] == "standard"
    # The main checkout routes automatically, so `--config` stays refused from the worktree too.
    extra = tmp_path / "extra.toml"
    extra.write_text("", encoding="utf-8")
    code, _, err = run(worktree, "raise", "deep", "--reason", WIDER, "--config", str(extra))
    assert code == 1 and "--config is not accepted" in err
    code, out, err = run(worktree, "raise", "deep", "--reason", WIDER)
    assert code == 0, err
    new = RecordStore(python_project).read("routes")[-1]
    assert new["raised_from"] == old["id"] and new["auto"] is True and new["profile"] == "deep"
    assert new["id"] in out
    # The main checkout's configuration gates the route, so its approval rule applies.
    assert "approval again" in out


def test_raise_takes_the_route_the_hooks_track_over_a_newer_one(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    """The session started in the main checkout; a stray route in the worktree is newer."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    main_root, worktree = repo
    tracked = route_in(main_root, monkeypatch, task=STANDARD_TASK)
    hook = SessionState(main_root, SESSION)
    hook.for_record(tracked)["work_started"] = True
    hook.save()
    stray = route_in(worktree, monkeypatch, task=STANDARD_TASK)
    code, _, err = run(worktree, "raise", "deep", "--reason", WIDER)
    assert code == 0, err
    new = RecordStore(main_root).read("routes")[-1]
    assert new["profile"] == "deep" and new["raised_from"] == tracked
    # The main checkout's Stop hook enforces the raised route; the worktree's route is untouched.
    assert f"DEEP, record {new['id']}" in json.loads(stop_in(main_root))["reason"]
    assert [r["id"] for r in RecordStore(worktree).read("routes")] == [stray]
