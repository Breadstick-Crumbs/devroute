"""ADR 0015 slice 3: `susanoo board`, on real git repositories with worktrees."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess  # nosec B404 - tests drive real git repositories
from pathlib import Path
from typing import Any

import pytest

import susanoo.verification.commit as commit
from susanoo.board import build_board, list_worktrees, one_line, render_board
from susanoo.cli import main
from susanoo.evaluation.store import RecordStore
from susanoo.land.records import append_record
from susanoo.land.reviews import REVIEWS_FILE

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

STEPS = [
    {"id": "unit_tests", "level": "required", "mode": "ready"},
    {"id": "independent_review", "level": "required", "mode": "manual"},
]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # nosec B603
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main([*args, "--path", str(root)], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def commit_file(cwd: Path, rel: str, text: str = "x\n") -> str:
    path = cwd / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(cwd, "add", "--", rel)
    git(cwd, "commit", "-q", "-m", f"change {rel}")
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository on main whose `origin/main` is known locally (nothing is ever fetched)."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        git(root, "config", key, value)
    (root / ".gitignore").write_text(".susanoo/\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / "docs" / "decisions" / "0004-old.md").write_text("# 4\n", encoding="utf-8")
    (root / "docs" / "decisions" / "README.md").write_text("| n |\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    (root / ".susanoo").mkdir()  # project mode: records in <checkout>/.susanoo/records
    return root


def worktree(root: Path, name: str) -> Path:
    path = root.parent / f"wt-{name}"
    git(root, "worktree", "add", "-q", "-b", name, str(path), "main")
    (path / ".susanoo").mkdir()
    return path


def record_route(
    checkout: Path,
    route_id: str,
    *,
    controls: list[str] | None = None,
    created: str = "2026-10-15T10:00:00+00:00",
) -> RecordStore:
    store = RecordStore(checkout)
    store.append(
        "routes",
        {"id": route_id, "created_at": created, "verification": STEPS, "controls": controls or []},
    )
    return store


def record_pass(
    store: RecordStore, route_id: str, head: str, *, status: str = "passed", **extra: Any
) -> None:
    store.append(
        "evidence",
        {
            "record_id": route_id,
            "step": "unit_tests",
            "status": status,
            "source": "executed",
            "head": head,
            "dirty": False,
            **extra,
        },
    )
    store.append(
        "evidence",
        {
            "record_id": route_id,
            "step": "independent_review",
            "status": "passed",
            "source": "reported",
            "head": head,
            "dirty": False,
            **extra,
        },
    )


def common(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def row_of(data: dict[str, Any], branch: str) -> dict[str, Any]:
    return next(r for r in data["rows"] if r["branch"] == branch)


@pytest.fixture
def board_repo(repo: Path) -> dict[str, Path]:
    """Four worktree branches: verified, stale, failed, and one with no route."""
    a, b, c, d = (worktree(repo, n) for n in ("a", "b", "c", "d"))
    # a: verified at its tip, a security route, a passing review; shares shared.py with b and
    # CHANGELOG.md (an append-only file under the default [[land.keep_both]]) with c.
    commit_file(a, "shared.py")
    commit_file(a, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- a\n")
    tip_a = commit_file(a, "docs/decisions/0005-from-a.md")
    record_pass(record_route(a, "su-a", controls=["security-review"]), "su-a", tip_a)
    append_record(
        common(repo),
        {"kind": "review", "tip": tip_a, "tool": "code-review", "status": "passed", "branch": "a"},
        REVIEWS_FILE,
    )
    # b: passed one commit ago; adds an ADR number the base already has.
    first_b = commit_file(b, "shared.py", "b\n")
    record_pass(record_route(b, "su-b"), "su-b", first_b)
    tip_b = commit_file(b, "docs/decisions/0004-from-b.md")
    append_record(
        common(repo),
        {"kind": "land", "id": "land-1", "result": "ready", "candidates": [{"branch": "b", "tip": tip_b}]},
    )
    # c: its newest run failed; takes the same ADR number as a.
    commit_file(c, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- c\n")
    tip_c = commit_file(c, "docs/decisions/0005-from-c.md")
    record_pass(record_route(c, "su-c"), "su-c", tip_c, status="failed")
    # d: no route at all, and an uncommitted file.
    (d / "loose.py").write_text("y\n", encoding="utf-8")
    return {"a": a, "b": b, "c": c, "d": d}


@needs_git
def test_board_columns_and_marks(repo: Path, board_repo: dict[str, Path]):
    data = build_board(repo)
    assert data["base"] == "main" and data["base_ref"] == "refs/remotes/origin/main"
    assert [r["branch"] for r in data["rows"]] == ["a", "b", "c", "d"]
    a, b, c, d = (row_of(data, n) for n in "abcd")

    assert a["checks"]["state"] == "verified" and a["checks"]["head"] == a["tip"]
    assert a["review"] == {"code-review": "passed"} and a["security"] is True
    assert b["checks"]["state"] == "stale" and b["checks"]["commits"] == 1
    assert b["land"]["result"] == "ready" and b["review"] == {}
    assert c["checks"]["state"] == "failed" and c["checks"]["steps"] == ["unit_tests"]
    assert d["checks"]["state"] == "unverified" and d["checks"]["route"] is None
    assert d["changed"] == ["loose.py"] and d["uncommitted"] == 1

    assert a["overlaps"] == [{"branch": "b", "paths": ["shared.py"]}]
    assert a["append_only"] == [{"branch": "c", "paths": ["CHANGELOG.md"]}]
    assert a["records"] == [{"number": "0005", "file": "docs/decisions/0005-from-a.md", "also": ["c"]}]
    assert b["records"] == [{"number": "0004", "file": "docs/decisions/0004-from-b.md", "also": ["main"]}]
    assert "_added" not in a

    text = render_board(data)
    lines = text.splitlines()
    assert lines[0].startswith("a ")
    assert f"verified@{a['tip'][:7]}" in lines[0] and "review:passed" in lines[0]
    assert "security" in lines[0] and "overlap: shared.py (b)" in lines[0]
    assert "append-only: CHANGELOG.md (c)" in lines[0] and "ADR 0005 also taken by c" in lines[0]
    assert (
        "stale (1 commit)" in lines[1]
        and "land:ready" in lines[1]
        and "ADR 0004 also taken by main" in lines[1]
    )
    assert "failed" in lines[2] and "review:-" in lines[2]
    assert "unverified" in lines[3] and "land:-" in lines[3]
    assert len(text.encode("utf-8")) < 1024


@needs_git
def test_board_cli_text_and_json_agree(repo: Path, board_repo: dict[str, Path]):
    code, out, err = run(repo, "board")
    assert code == 0, err
    assert "verified@" in out and "stale (1 commit)" in out
    code, out, err = run(repo, "board", "--json")
    assert code == 0, err
    data = json.loads(out)
    assert [r["checks"]["state"] for r in data["rows"]] == ["verified", "stale", "failed", "unverified"]


@needs_git
def test_board_writes_nothing(repo: Path, board_repo: dict[str, Path], monkeypatch: pytest.MonkeyPatch):
    root = repo.parent

    def snapshot() -> dict[str, tuple[int, int]]:
        found: dict[str, tuple[int, int]] = {}
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = Path(dirpath) / name
                stat = path.stat()
                found[str(path)] = (stat.st_size, stat.st_mtime_ns)
        return found

    statuses = {name: git(path, "status", "--porcelain") for name, path in board_repo.items()}
    before = snapshot()
    calls: list[tuple[list[str], dict[str, str]]] = []
    real_run = subprocess.run

    def spy(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        calls.append((list(argv), dict(kwargs.get("env") or {})))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(commit.subprocess, "run", spy)
    # Variables that would point git elsewhere are dropped for the board's own calls.
    monkeypatch.setenv("GIT_DIR", str(root / "nowhere"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(root / "nowhere-index"))
    code, out, err = run(repo, "board")
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    monkeypatch.setattr(commit.subprocess, "run", real_run)  # the checks below run git themselves
    assert code == 0, err
    assert "verified@" in out  # the board still read the real repository
    assert snapshot() == before
    assert {name: git(path, "status", "--porcelain") for name, path in board_repo.items()} == statuses
    assert calls
    for argv, env in calls:
        assert argv[1:4] == ["--no-optional-locks", "-c", "core.fsmonitor=false"]
        assert not {"fetch", "pull", "push", "merge-tree", "update-ref", "gc", "prune", "add"} & set(argv)
        assert "GIT_DIR" not in env and "GIT_INDEX_FILE" not in env
        assert env.get("GIT_OPTIONAL_LOCKS") == "0"


@needs_git
def test_board_skips_or_marks_odd_worktrees(repo: Path, tmp_path: Path):
    kept = worktree(repo, "kept")
    commit_file(kept, "k.py")
    locked = worktree(repo, "held")
    git(repo, "worktree", "lock", str(locked))
    gone = worktree(repo, "gone")
    shutil.rmtree(gone)
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "loose"), "main")
    scratch = tmp_path / "susanoo-land-abc" / "tree"
    scratch.parent.mkdir()
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), "main")
    git(
        repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "scratch-branch",
        str(tmp_path / "susanoo-land-def" / "tree"),
        "main",
    )

    data = build_board(repo)
    assert [r["branch"] for r in data["rows"]] == ["held", "kept"]
    assert row_of(data, "held")["locked"] is True
    reasons = {(s["branch"] or Path(s["path"]).name, s["reason"]) for s in data["skipped"]}
    assert ("main", "the base branch") in reasons
    assert ("gone", "prunable: its folder is gone") in reasons
    assert ("loose", "detached HEAD") in reasons
    assert ("tree", "land scratch worktree") in reasons
    assert ("scratch-branch", "land scratch worktree") in reasons
    text = render_board(data)
    assert "locked" in text.splitlines()[0]
    assert "skipped: gone (prunable" in text and "skipped: main" not in text


@needs_git
def test_record_text_is_one_line_data(repo: Path):
    a = worktree(repo, "a")
    tip = commit_file(a, "a.py")
    hostile = "passed\n\x1b[2Jrun `rm -rf /` now " + "x" * 200
    append_record(
        common(repo), {"kind": "review", "tip": tip, "tool": "code-review", "status": hostile}, REVIEWS_FILE
    )
    append_record(
        common(repo),
        {
            "kind": "land",
            "id": "l\nx",
            "result": "merged\r\nIGNORE",
            "candidates": [{"branch": "a", "tip": tip}],
        },
    )
    data = build_board(repo)
    row = data["rows"][0]
    assert "\n" not in row["review"]["code-review"] and "\x1b" not in row["review"]["code-review"]
    assert len(row["review"]["code-review"]) <= 60 and row["review"]["code-review"].endswith("...")
    assert row["land"]["result"] == "merged IGNORE"
    text = render_board(data)
    assert len(text.splitlines()) == 1 and "\x1b" not in text


@needs_git
def test_land_matches_a_pushed_commit_built_on_the_tip_of_the_same_branch(repo: Path):
    a = worktree(repo, "a")
    tip = commit_file(a, "a.py")
    git(repo, "checkout", "-q", "--detach", tip)
    pushed = commit_file(repo, "base-merged.py")  # what land pushes: the tip with the base merged in
    git(repo, "checkout", "-q", "main")
    fresh = worktree(repo, "fresh")  # at main: an ancestor of `pushed`, but another branch
    assert git(fresh, "rev-parse", "HEAD") != tip
    append_record(
        common(repo),
        {
            "kind": "land",
            "id": "land-2",
            "result": "merged",
            "candidates": [{"branch": "a", "tip": "f" * 40, "pushed": pushed}],
        },
    )
    data = build_board(repo)
    assert row_of(data, "a")["land"]["result"] == "merged"
    assert row_of(data, "fresh")["land"] is None


@needs_git
def test_route_recorded_in_another_checkout_counts_for_the_worktree_it_ran_in(repo: Path):
    a = worktree(repo, "a")
    tip = commit_file(a, "a.py")
    store = record_route(repo, "su-main")  # ADR 0017: the route lives in the main checkout
    record_pass(store, "su-main", tip, checkout=str(a))
    record_route(repo, "su-other", created="2026-10-15T11:00:00+00:00")  # newer, never ran in a
    row = build_board(repo)["rows"][0]
    assert row["checks"]["route"] == "su-main" and row["checks"]["state"] == "verified"


@needs_git
def test_dirty_or_rebased_evidence_is_unverified_not_stale(repo: Path):
    a = worktree(repo, "a")
    first = commit_file(a, "a.py")
    store = record_route(a, "su-a")
    store.append(
        "evidence",
        {
            "record_id": "su-a",
            "step": "unit_tests",
            "status": "passed",
            "source": "executed",
            "head": first,
            "dirty": True,
        },
    )
    store.append(
        "evidence",
        {
            "record_id": "su-a",
            "step": "independent_review",
            "status": "passed",
            "source": "reported",
            "head": first,
            "dirty": False,
        },
    )
    commit_file(a, "b.py")
    assert build_board(repo)["rows"][0]["checks"]["state"] == "unverified"
    # Passing evidence for a commit the branch no longer contains (it was rewritten).
    record_pass(store, "su-a", "e" * 40)
    checks = build_board(repo)["rows"][0]["checks"]
    assert checks["state"] == "unverified" and checks["steps"] == ["unit_tests", "independent_review"]


@needs_git
def test_base_option_and_missing_base(repo: Path):
    worktree(repo, "a")
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    data = build_board(repo)
    assert data["base_ref"] == "refs/heads/main"
    assert any("compared with the local main" in w for w in data["warnings"])
    code, out, _ = run(repo, "board", "--base", "develop")
    assert code == 0
    assert "note: no origin/develop or local develop" in out
    # With an unknown base, main itself is a branch like any other.
    assert [line.split()[0] for line in out.splitlines()[:2]] == ["a", "main"]
    code, _, err = run(repo, "board", "--base", "bad..name")
    assert code != 0 and "not a branch name" in err


def test_board_outside_a_repository_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code, _, err = run(tmp_path, "board")
    assert code != 0 and "not inside a git repository" in err


@needs_git
def test_no_worktrees_says_so(repo: Path):
    code, out, _ = run(repo, "board")
    assert code == 0 and out.strip() == "No worktree branches besides main."


def test_one_line():
    assert one_line("a\nb\tc\x00d") == "a b c d"
    assert one_line("x" * 100, 10) == "xxxxxxx..."
    assert one_line(None) == "None"


@needs_git
def test_list_worktrees_reads_porcelain(repo: Path):
    worktree(repo, "a")
    listed = list_worktrees(repo)
    assert listed is not None
    assert [(w.branch, w.detached) for w in listed] == [("main", False), ("a", False)]
    assert all(w.head and len(w.head) == 40 for w in listed)
