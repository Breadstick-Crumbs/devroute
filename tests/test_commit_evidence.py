"""ADR 0015 slice 1: evidence bound to a commit, "verified" routes, and review records."""

from __future__ import annotations

import io
import json
import shutil
import subprocess  # nosec B404 - tests drive real git repositories
from pathlib import Path
from typing import Any

import pytest

import susanoo.cli as cli
import susanoo.verification.commit as commit
from susanoo.cli import main
from susanoo.enforcement.state import evidence_status, is_verified, unverified_steps
from susanoo.evaluation.store import RecordStore
from susanoo.land import LandError
from susanoo.land import reviews as reviews_mod
from susanoo.land.records import land_records_path
from susanoo.land.reviews import read_reviews, review_for
from susanoo.verification.commit import UNKNOWN, TreeState, across, tree_state
from susanoo.verification.executor import Evidence

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

SHA = "a" * 40
OTHER = "b" * 40


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # nosec B603
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([*args, "--path", str(root)], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        git(path, "config", key, value)
    (path / "app.py").write_text("X = 1\n", encoding="utf-8")
    (path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path


@pytest.fixture
def ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep git from finding a repository above the test's folder."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path


# --- the commit and the state of a checkout ---------------------------------------------


@needs_git
def test_tree_state_reads_head_and_dirty(tmp_path: Path, ceiling: Path):
    repo = make_repo(tmp_path / "repo")
    head = git(repo, "rev-parse", "HEAD")
    assert tree_state(repo) == TreeState(head, False)
    (repo / "debug.log").write_text("ignored\n", encoding="utf-8")
    assert tree_state(repo) == TreeState(head, False)  # ignored files do not count
    (repo / "new.py").write_text("Y = 2\n", encoding="utf-8")
    assert tree_state(repo) == TreeState(head, True)  # an untracked file the checks may run on
    (repo / "new.py").unlink()
    (repo / "app.py").write_text("X = 2\n", encoding="utf-8")
    assert tree_state(repo) == TreeState(head, True)
    git(repo, "add", "app.py")
    assert tree_state(repo) == TreeState(head, True)  # staged but not committed


@needs_git
def test_tree_state_does_not_vouch_for_files_git_status_skips(tmp_path: Path, ceiling: Path):
    repo = make_repo(tmp_path / "repo")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--assume-unchanged", "app.py")
    (repo / "app.py").write_text("X = 2\n", encoding="utf-8")
    assert tree_state(repo) == TreeState(head, None)  # status says clean; it did not look
    git(repo, "update-index", "--no-assume-unchanged", "app.py")
    git(repo, "checkout", "--", "app.py")
    git(repo, "update-index", "--skip-worktree", "app.py")
    assert tree_state(repo) == TreeState(head, None)
    git(repo, "update-index", "--no-skip-worktree", "app.py")
    assert tree_state(repo) == TreeState(head, False)


@needs_git
def test_tree_state_ignores_configuration_from_the_environment(
    tmp_path: Path, ceiling: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = make_repo(tmp_path / "repo")
    (repo / "new.py").write_text("Y = 2\n", encoding="utf-8")
    exclude = tmp_path / "exclude"
    exclude.write_text("new.py\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.excludesFile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(exclude))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.excludesfile'='{exclude}'")
    assert tree_state(repo).dirty is True


@needs_git
def test_tree_state_is_unknown_outside_a_repository_or_without_a_commit(tmp_path: Path, ceiling: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert tree_state(plain) == UNKNOWN
    assert tree_state(tmp_path / "missing") == UNKNOWN
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    git(unborn, "init", "-q")
    assert tree_state(unborn) == UNKNOWN
    fake = tmp_path / "fake"
    (fake / ".git").mkdir(parents=True)  # not a repository git can read
    assert tree_state(fake) == UNKNOWN


def test_tree_state_is_unknown_without_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(commit.shutil, "which", lambda _name: None)
    assert tree_state(tmp_path) == UNKNOWN


@needs_git
def test_tree_state_ignores_variables_pointing_at_another_repository(
    tmp_path: Path, ceiling: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = make_repo(tmp_path / "repo")
    other = make_repo(tmp_path / "other")
    (other / "app.py").write_text("changed\n", encoding="utf-8")
    git(other, "commit", "-qam", "moves")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES")
    state = tree_state(repo)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert state == TreeState(git(repo, "rev-parse", "HEAD"), False)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (TreeState(SHA, False), TreeState(SHA, False), TreeState(SHA, False)),
        (TreeState(SHA, True), TreeState(SHA, False), TreeState(SHA, True)),
        (TreeState(SHA, False), TreeState(SHA, True), TreeState(SHA, True)),  # a check left changes
        (TreeState(SHA, False), TreeState(OTHER, False), TreeState(SHA, True)),  # HEAD moved meanwhile
        (TreeState(SHA, False), UNKNOWN, TreeState(SHA, None)),
        (TreeState(SHA, None), TreeState(SHA, False), TreeState(SHA, None)),
        (TreeState(SHA, None), TreeState(SHA, True), TreeState(SHA, True)),
        (UNKNOWN, TreeState(SHA, False), UNKNOWN),
    ],
)
def test_across_is_clean_only_when_both_sides_are(before: TreeState, after: TreeState, expected: TreeState):
    assert across(before, after) == expected


# --- verify and evidence record it themselves -------------------------------------------


@pytest.fixture
def project(tmp_path: Path, ceiling: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository in project mode whose checks all pass (execution is replaced)."""
    repo = make_repo(tmp_path / "proj")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1"\n[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    (repo / ".susanoo").mkdir()
    (repo / ".susanoo" / ".gitignore").write_text("records/\n", encoding="utf-8")  # as `susanoo init` writes
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "project")

    def fake_execute(plan, root, **_kwargs):  # type: ignore[no-untyped-def]
        return [
            Evidence(s.id, s.capability, "passed", "executed", ["true"], 0, 0.1, "2026-10-15T00:00:00+00:00")
            for s in plan.steps
            if s.mode == "ready"
        ]

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    return repo


def route(root: Path, task: str = "Refactor the parser module and add unit tests") -> dict[str, Any]:
    code, _, err = run(root, "route", task)
    assert code == 0, err
    return RecordStore(root).read("routes")[-1]


def not_runnable(record: dict[str, Any]) -> list[str]:
    """The route's required steps that Susanoo cannot run (they depend on the installed tools)."""
    return [s["id"] for s in record["verification"] if s["level"] == "required" and s["mode"] != "ready"]


@needs_git
def test_verify_binds_executed_evidence_to_the_commit(project: Path):
    record = route(project)
    head = git(project, "rev-parse", "HEAD")
    code, _, err = run(project, "verify", "--execute", "--record", record["id"])
    assert code == 0, err
    evidence = RecordStore(project).read("evidence")
    assert evidence and all(e["head"] == head and e["dirty"] is False for e in evidence)
    assert all("checkout" not in e and "commit" not in e for e in evidence)
    # Steps Susanoo cannot run here (the independent review; checks whose tool is not installed)
    # only a report can cover.
    reported = not_runnable(record)
    assert "independent_review" in reported
    assert unverified_steps(record, evidence, head) == reported
    for step in reported:
        code, _, err = run(project, "evidence", record["id"], "--step", step, "--status", "passed")
        assert code == 0, err
    evidence = RecordStore(project).read("evidence")
    assert is_verified(record, evidence, head)
    assert not is_verified(record, evidence, OTHER)


@needs_git
def test_verify_on_uncommitted_changes_is_dirty_and_not_verified(project: Path):
    record = route(project)
    (project / "app.py").write_text("X = 3\n", encoding="utf-8")
    assert run(project, "verify", "--execute", "--record", record["id"])[0] == 0
    evidence = RecordStore(project).read("evidence")
    assert {e["dirty"] for e in evidence} == {True}
    # The evidence gate still accepts the runs; they just do not hold for any commit.
    assert evidence_status(record, evidence) == (not_runnable(record), [])
    assert "unit_tests" in unverified_steps(record, evidence, git(project, "rev-parse", "HEAD"))


@needs_git
def test_a_check_that_changes_files_makes_its_evidence_dirty(project: Path, monkeypatch: pytest.MonkeyPatch):
    def writes(plan, root, **_kwargs):  # type: ignore[no-untyped-def]
        (Path(root) / "generated.py").write_text("Z = 1\n", encoding="utf-8")
        return [
            Evidence(s.id, s.capability, "passed", "executed", ["true"], 0, 0.1, "2026-10-15T00:00:00+00:00")
            for s in plan.steps
            if s.mode == "ready"
        ]

    monkeypatch.setattr(cli, "execute_plan", writes)
    record = route(project)
    assert run(project, "verify", "--execute", "--record", record["id"])[0] == 0
    assert {e["dirty"] for e in RecordStore(project).read("evidence")} == {True}


@needs_git
def test_reported_evidence_gets_the_commit_from_susanoo(project: Path):
    record = route(project)
    head = git(project, "rev-parse", "HEAD")
    code, out, err = run(
        project, "evidence", record["id"], "--step", "independent_review", "--status", "passed"
    )
    assert code == 0, err
    item = RecordStore(project).read("evidence")[-1]
    assert item["source"] == "reported" and item["head"] == head and item["dirty"] is False
    # There is no way for the agent to supply the commit.
    for flag in ("--head", "--dirty"):
        with pytest.raises(SystemExit):
            main(["evidence", record["id"], "--step", "x", "--status", "passed", flag, OTHER])


@needs_git
def test_reported_evidence_on_a_dirty_tree_is_dirty(project: Path):
    record = route(project)
    (project / "app.py").write_text("X = 4\n", encoding="utf-8")
    assert (
        run(project, "evidence", record["id"], "--step", "independent_review", "--status", "passed")[0] == 0
    )
    assert RecordStore(project).read("evidence")[-1]["dirty"] is True


def test_evidence_outside_git_records_unknown(
    python_project: Path, ceiling: Path, monkeypatch: pytest.MonkeyPatch
):
    (python_project / ".susanoo").mkdir()
    record = route(python_project)
    assert run(python_project, "evidence", record["id"], "--step", "unit_tests", "--status", "passed")[0] == 0
    item = RecordStore(python_project).read("evidence")[-1]
    assert item["head"] is None and item["dirty"] is None


# --- "verified" (ADR 0015) --------------------------------------------------------------

ROUTE: dict[str, Any] = {
    "id": "su-1",
    "verification": [
        {"id": "unit_tests", "level": "required", "mode": "ready"},
        {"id": "independent_review", "level": "required", "mode": "manual"},
        {"id": "lint", "level": "recommended", "mode": "ready"},
    ],
}


def ev(step: str, status: str = "passed", source: str = "executed", **extra: Any) -> dict[str, Any]:
    return {"record_id": "su-1", "step": step, "status": status, "source": source, **extra}


def bound(
    step: str, status: str = "passed", source: str = "executed", head: str = SHA, dirty: Any = False
) -> dict[str, Any]:
    return ev(step, status, source, head=head, dirty=dirty)


def test_verified_needs_every_required_step_bound_to_the_tip():
    evidence = [bound("unit_tests"), bound("independent_review", source="reported")]
    assert is_verified(ROUTE, evidence, SHA)
    assert is_verified(ROUTE, evidence, SHA.upper())
    assert unverified_steps(ROUTE, evidence, OTHER) == ["unit_tests", "independent_review"]
    assert not is_verified(ROUTE, evidence, None)
    assert not is_verified(ROUTE, evidence, "")


def test_evidence_without_head_never_counts():
    old = [ev("unit_tests"), ev("independent_review", source="reported")]  # written before ADR 0015
    assert evidence_status(ROUTE, old) == ([], [])  # still readable, and the gate still accepts it
    assert unverified_steps(ROUTE, old, SHA) == ["unit_tests", "independent_review"]
    legacy = [ev("unit_tests", commit=SHA, checkout="/wt"), bound("independent_review", source="reported")]
    assert unverified_steps(ROUTE, legacy, SHA) == ["unit_tests"]  # ADR 0017's `commit` is not `head`
    unknown = [bound("unit_tests", dirty=None), bound("independent_review", source="reported", head=None)]
    assert unverified_steps(ROUTE, unknown, SHA) == ["unit_tests", "independent_review"]


def test_the_newest_evidence_decides():
    newer_dirty = [
        bound("unit_tests"),
        bound("unit_tests", dirty=True),
        bound("independent_review", source="reported"),
    ]
    assert unverified_steps(ROUTE, newer_dirty, SHA) == ["unit_tests"]
    newer_fail = [
        bound("unit_tests"),
        bound("unit_tests", "failed"),
        bound("independent_review", source="reported"),
    ]
    assert unverified_steps(ROUTE, newer_fail, SHA) == ["unit_tests"]
    older_commit = [
        bound("unit_tests"),
        bound("unit_tests", head=OTHER),
        bound("independent_review", source="reported"),
    ]
    assert unverified_steps(ROUTE, older_commit, SHA) == ["unit_tests"]
    fixed = [
        bound("unit_tests", "failed"),
        bound("unit_tests"),
        bound("independent_review", source="reported"),
    ]
    assert is_verified(ROUTE, fixed, SHA)


def test_runnable_steps_need_executed_runs():
    reported = [bound("unit_tests", source="reported"), bound("independent_review", source="reported")]
    assert unverified_steps(ROUTE, reported, SHA) == ["unit_tests"]
    # A skipped "run" of a runnable step is not a run; the earlier pass still decides.
    skipped = [
        bound("unit_tests"),
        bound("unit_tests", "skipped"),
        bound("independent_review", source="reported"),
    ]
    assert is_verified(ROUTE, skipped, SHA)
    # An explained skip of a step Susanoo cannot run is accepted, as the gate accepts it.
    review_skipped = [bound("unit_tests"), bound("independent_review", "skipped", source="reported")]
    assert is_verified(ROUTE, review_skipped, SHA)


def test_a_failure_in_another_checkout_keeps_the_route_unverified():
    evidence = [
        bound("unit_tests", "failed") | {"checkout": "/wt"},
        bound("unit_tests"),
        bound("independent_review", source="reported"),
    ]
    assert unverified_steps(ROUTE, evidence, SHA) == ["unit_tests"]


def test_other_routes_evidence_and_routes_without_required_steps():
    other = [bound("unit_tests") | {"record_id": "su-2"}, bound("independent_review", source="reported")]
    assert unverified_steps(ROUTE, other, SHA) == ["unit_tests"]
    empty = {"id": "su-1", "verification": [{"id": "lint", "level": "recommended", "mode": "ready"}]}
    assert unverified_steps(empty, [bound("lint")], SHA) == []
    assert not is_verified(empty, [bound("lint")], SHA)
    auto = {
        "id": "su-1",
        "auto": True,
        "verification": [
            *ROUTE["verification"][:1],
            {"id": "e2e", "level": "required", "mode": "unavailable"},
        ],
    }
    assert is_verified(
        auto, [bound("unit_tests")], SHA
    )  # as the gate: a missing tool is reported, not demanded


# --- review records ---------------------------------------------------------------------


@pytest.fixture
def remote_repo(tmp_path: Path, ceiling: Path) -> tuple[Path, Path]:
    """A clone of a bare origin with a pushed branch `feature` and a linked worktree."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "-q", "--bare", "-b", "main")
    work = make_repo(tmp_path / "work")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "feature")
    (work / "feature.py").write_text("F = 1\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "feature")
    git(work, "push", "-q", "origin", "feature")
    git(work, "checkout", "-q", "main")
    worktree = tmp_path / "linked"
    git(work, "worktree", "add", "-q", str(worktree), "-b", "other")
    return work, worktree


def review(root: Path, *args: str) -> tuple[int, str, str]:
    return run(root, "review", *args)


@needs_git
def test_review_record_needs_the_current_tip(remote_repo: tuple[Path, Path]):
    work, worktree = remote_repo
    tip = git(work, "rev-parse", "feature")
    base = git(work, "rev-parse", "main")
    code, _, err = review(
        work, "record", "feature", "--tip", base, "--status", "passed", "--tool", "code-review"
    )
    assert code == 1 and "older or other commit" in err
    code, _, err = review(
        work, "record", "feature", "--tip", "abc", "--status", "passed", "--tool", "code-review"
    )
    assert code == 1 and "at least 7" in err
    code, out, err = review(
        work,
        "record",
        "feature",
        "--tip",
        tip[:9],
        "--status",
        "passed",
        "--tool",
        "code-review",
        "--note",
        "ok\nno findings",
    )
    assert code == 0, err
    assert "code-review passed for feature" in out
    common = Path(git(work, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    records = read_reviews(common)
    assert len(records) == 1
    assert records[0] | {"id": "", "recorded_at": ""} == {
        "kind": "review",
        "id": "",
        "recorded_at": "",
        "target": "feature",
        "branch": "feature",
        "pr": None,
        "tip": tip,
        "tool": "code-review",
        "status": "passed",
        "note": "ok no findings",
        "session_id": None,
        "checkout": str(work.resolve()),
    }
    assert land_records_path(common, "reviews.jsonl").parent == land_records_path(common).parent
    # Every worktree of the repository reads the same records.
    code, out, err = review(worktree, "list", "--json")
    assert code == 0, err
    assert [r["tip"] for r in json.loads(out)] == [tip]


@needs_git
def test_review_record_follows_the_branch_as_it_moves(remote_repo: tuple[Path, Path]):
    work, _ = remote_repo
    old = git(work, "rev-parse", "feature")
    git(work, "checkout", "-q", "feature")
    (work / "feature.py").write_text("F = 2\n", encoding="utf-8")
    git(work, "commit", "-qam", "more")
    new = git(work, "rev-parse", "HEAD")
    # Local is ahead of origin: the local branch is the tip.
    assert (
        review(work, "record", "feature", "--tip", old, "--status", "passed", "--tool", "code-review")[0] == 1
    )
    assert (
        review(work, "record", "feature", "--tip", new, "--status", "failed", "--tool", "security-review")[0]
        == 0
    )
    # Someone else pushed a newer commit: the remote copy is the tip.
    git(work, "reset", "-q", "--hard", old)
    git(work, "checkout", "-q", "main")
    git(work, "push", "-q", "origin", f"{new}:refs/heads/feature")
    git(work, "update-ref", "refs/remotes/origin/feature", old)  # a stale tracking ref is refreshed
    assert (
        review(work, "record", "feature", "--tip", old, "--status", "passed", "--tool", "code-review")[0] == 1
    )
    assert (
        review(work, "record", "feature", "--tip", new, "--status", "passed", "--tool", "code-review")[0] == 0
    )


@needs_git
def test_review_record_refuses_when_the_remote_cannot_be_reached(
    remote_repo: tuple[Path, Path], tmp_path: Path
):
    work, _ = remote_repo
    old = git(work, "rev-parse", "feature")
    # Another clone pushes a newer commit; this clone has fetched it, but its local branch is older.
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    git(other, "checkout", "-q", "feature")
    for key, value in (("user.name", "T"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(other, "config", key, value)
    git(other, "commit", "-q", "--allow-empty", "-m", "newer")
    git(other, "push", "-q", "origin", "feature")
    git(work, "fetch", "-q", "origin")
    newer = git(work, "rev-parse", "origin/feature")
    assert newer != old
    git(work, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    for tip in (old, newer):  # neither can be confirmed as the current tip
        code, _, err = review(
            work, "record", "feature", "--tip", tip, "--status", "passed", "--tool", "code-review"
        )
        assert code == 1 and "could not reach origin" in err
    code, _, err = review(
        work,
        "record",
        "feature",
        "--tip",
        old,
        "--status",
        "passed",
        "--tool",
        "code-review",
        "--remote",
        "nowhere",
    )
    assert code == 0, err  # no such remote: the branch is local only
    common = Path(git(work, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert [r["tip"] for r in read_reviews(common)] == [old]


@needs_git
def test_review_record_of_a_branch_never_pushed(remote_repo: tuple[Path, Path]):
    work, _ = remote_repo
    git(work, "checkout", "-q", "-b", "local-only")
    (work / "local.py").write_text("L = 1\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "local")
    tip = git(work, "rev-parse", "HEAD")
    code, _, err = review(
        work, "record", "local-only", "--tip", tip, "--status", "passed", "--tool", "code-review"
    )
    assert code == 0, err


@needs_git
def test_review_record_refuses_diverged_unknown_or_odd_branches(remote_repo: tuple[Path, Path]):
    work, _ = remote_repo
    tip = git(work, "rev-parse", "feature")
    git(work, "checkout", "-q", "feature")
    (work / "feature.py").write_text("local\n", encoding="utf-8")
    git(work, "commit", "-qam", "local")
    git(work, "checkout", "-q", "-b", "elsewhere", "main")
    (work / "feature.py").write_text("elsewhere\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "elsewhere")
    git(work, "push", "-q", "-f", "origin", "elsewhere:feature")  # neither copy contains the other
    git(work, "checkout", "-q", "main")
    code, _, err = review(
        work, "record", "feature", "--tip", tip, "--status", "passed", "--tool", "code-review"
    )
    assert code == 1 and "diverged" in err
    code, _, err = review(work, "record", "nope", "--tip", tip, "--status", "passed", "--tool", "code-review")
    assert code == 1 and "no branch named nope" in err
    code, _, err = review(work, "record", "a..b", "--tip", tip, "--status", "passed", "--tool", "code-review")
    assert code == 1 and "not a pull request number or a valid branch name" in err
    for odd in ("--upload-pack=x", "-b"):  # never handed to git as an option
        with pytest.raises(LandError, match="valid branch name"):
            reviews_mod.resolve_target(work, odd)
    with pytest.raises(LandError, match="not a remote name"):
        reviews_mod.resolve_target(work, "feature", "--upload-pack=x")
    assert read_reviews(Path(git(work, "rev-parse", "--path-format=absolute", "--git-common-dir"))) == []


def test_review_record_choices_are_fixed(tmp_path: Path):
    for args in (
        ["--status", "maybe", "--tool", "code-review"],
        ["--status", "passed", "--tool", "lint"],
        ["--status", "passed"],
    ):
        with pytest.raises(SystemExit):
            main(["review", "record", "feature", "--tip", SHA, *args, "--path", str(tmp_path)])


def test_review_outside_a_repository_is_an_error(tmp_path: Path, ceiling: Path):
    code, _, err = review(
        tmp_path, "record", "feature", "--tip", SHA, "--status", "passed", "--tool", "code-review"
    )
    assert code == 1 and "not inside a git repository" in err
    code, _, err = review(tmp_path, "list")
    assert code == 1 and "not inside a git repository" in err


@needs_git
def test_review_record_of_a_pull_request_uses_its_head_on_github(
    remote_repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    work, _ = remote_repo
    calls: list[tuple[str, ...]] = []

    def fake_gh_json(_root: Path, *args: str) -> Any:
        calls.append(args)
        return {"number": 57, "headRefName": "feature", "headRefOid": SHA.upper()}

    monkeypatch.setattr(reviews_mod, "gh_available", lambda: True)
    monkeypatch.setattr(reviews_mod, "gh_json", fake_gh_json)
    code, _, err = review(
        work, "record", "#57", "--tip", OTHER, "--status", "passed", "--tool", "code-review"
    )
    assert code == 1 and "#57 is at" in err
    code, _, err = review(work, "record", "57", "--tip", SHA, "--status", "passed", "--tool", "code-review")
    assert code == 0, err
    assert calls[-1][:3] == ("pr", "view", "57")
    code, out, _ = review(work, "list", "#57")
    assert "#57" in out and SHA[:12] in out
    assert review(work, "list", "feature")[1].count("#57") == 1
    assert "No review records for 58" in review(work, "list", "58")[1]
    for bad in (None, [], {"headRefOid": SHA[:12]}, {"headRefOid": "z" * 40}):
        monkeypatch.setattr(reviews_mod, "gh_json", lambda _root, *_args, value=bad: value)
        code, _, err = review(
            work, "record", "57", "--tip", SHA, "--status", "passed", "--tool", "code-review"
        )
        assert code == 1 and "no head commit" in err
    # Only ASCII digits name a pull request.
    code, _, err = review(
        work, "record", "\u00b2", "--tip", SHA, "--status", "passed", "--tool", "code-review"
    )
    assert code == 1 and "#" not in err
    assert review(work, "list", "\u00b2") == (0, "No review records for \u00b2.\n", "")
    monkeypatch.setattr(reviews_mod, "gh_available", lambda: False)
    code, _, err = review(work, "record", "57", "--tip", SHA, "--status", "passed", "--tool", "code-review")
    assert code == 1 and "`gh`" in err


def test_review_for_takes_the_newest_review_of_exactly_that_tip():
    records = [
        {"kind": "review", "tip": SHA, "tool": "code-review", "status": "failed"},
        {"kind": "review", "tip": SHA, "tool": "security-review", "status": "passed"},
        {"kind": "review", "tip": SHA, "tool": "code-review", "status": "passed"},
        {"kind": "review", "tip": OTHER, "tool": "code-review", "status": "failed"},
    ]
    assert (review_for(records, SHA.upper()) or {}).get("status") == "passed"
    assert (review_for(records, SHA, "security-review") or {}).get("status") == "passed"
    assert review_for(records, OTHER, "security-review") is None
    assert review_for(records, SHA[:12]) is None  # an exact commit, never a prefix
