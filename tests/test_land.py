"""`susanoo land` (ADR 0014): keep-both resolution, fixture repositories, and the merge gate."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from susanoo.cli import main
from susanoo.enforcement.gates import pre_tool_use
from susanoo.land import runner as land_runner
from susanoo.land.keepboth import parse_rules, resolve_text
from susanoo.land.records import land_records_path, read_records, stats
from susanoo.land.runner import _merge_body, _merge_subject

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

RULES = parse_rules(
    [
        {"path": "CHANGELOG.md", "section": r"^## \[Unreleased\]"},
        {"path": "docs/decisions/README.md", "lines": r"^\|", "sort": True},
    ]
)


# ---------------------------------------------------------------------------
# Keep-both resolution (pure)
# ---------------------------------------------------------------------------


def _conflict(ours: str, theirs: str, base: str = "", before: str = "## [Unreleased]\n\n### Added\n") -> str:
    return (
        f"# Changelog\n\n{before}<<<<<<< HEAD\n{ours}||||||| base\n{base}=======\n{theirs}>>>>>>> feature\n"
        "\n## [0.1.0]\n"
    )


def test_keep_both_appends_base_side_first_whichever_side_is_ours():
    text = _conflict("- from main\n", "- from branch\n")
    as_ours = resolve_text(text, "CHANGELOG.md", RULES, base_side="ours")
    swapped = _conflict("- from branch\n", "- from main\n")
    as_theirs = resolve_text(swapped, "CHANGELOG.md", RULES, base_side="theirs")
    assert as_ours.resolved and as_theirs.resolved
    assert as_ours.text == as_theirs.text
    assert "- from main\n- from branch\n" in (as_ours.text or "")
    assert "<<<<<<<" not in (as_ours.text or "")


def test_keep_both_preserves_crlf_line_endings():
    text = _conflict("- a\n", "- b\n").replace("\n", "\r\n")
    result = resolve_text(text, "CHANGELOG.md", RULES, base_side="ours")
    assert result.resolved
    assert "- a\r\n- b\r\n" in (result.text or "")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (_conflict("- a\n", "- b\n", base="- old\n"), "changed or removed"),
        (_conflict("### Fixed\n- a\n", "- b\n"), "heading"),
        (_conflict("- a\n", "- b\n", before="## [0.1.0]\n\n"), "outside the allowed section"),
        ("<<<<<<< HEAD\n- a\n=======\n- b\n>>>>>>> x\n", "no base part"),
        ("<<<<<<< HEAD\n- a\n||||||| base\n=======\n- b\n", "unreadable"),
    ],
)
def test_keep_both_refuses_anything_but_pure_additions(text: str, reason: str):
    result = resolve_text(text, "CHANGELOG.md", RULES, base_side="ours")
    assert not result.resolved
    assert reason in result.reason


def test_keep_both_only_in_configured_files():
    result = resolve_text(_conflict("- a\n", "- b\n"), "src/app.py", RULES, base_side="ours")
    assert not result.resolved and result.reason == "not an append-only place"


def test_keep_both_sorts_table_rows_and_requires_rows():
    table = "| ADR | Decision |\n|---|---|\n| [0001](a.md) | one |\n"
    text = (
        table + "<<<<<<< HEAD\n| [0003](c.md) | three |\n||||||| base\n=======\n"
        "| [0002](b.md) | two |\n>>>>>>> x\n"
    )
    result = resolve_text(text, "docs/decisions/README.md", RULES, base_side="ours")
    assert result.resolved
    assert (result.text or "").endswith("| [0002](b.md) | two |\n| [0003](c.md) | three |\n")
    prose = table + "<<<<<<< HEAD\nsome prose\n||||||| base\n=======\n| [0002](b.md) | two |\n>>>>>>> x\n"
    assert not resolve_text(prose, "docs/decisions/README.md", RULES, base_side="ours").resolved


def test_keep_both_rules_are_validated():
    from susanoo.config import ConfigError

    with pytest.raises(ConfigError):
        parse_rules([{"path": "x", "section": "("}])
    with pytest.raises(ConfigError):
        parse_rules([{"section": "x"}])
    assert parse_rules([{"path": "x", "enabled": False}]) == []


def test_merge_subject_and_body_follow_the_repository_style():
    pr = {
        "number": 52,
        "title": "Capture tokens from transcripts",
        "body": "## Summary\n\nReads usage from\ntranscripts.\n\n🤖 Generated with [Claude Code](https://claude.com)",
    }
    assert _merge_subject(pr) == "Merge: capture tokens from transcripts (#52)"
    assert (
        _merge_subject({"number": 3, "title": "UI domains in briefs"}) == "Merge: UI domains in briefs (#3)"
    )
    assert _merge_body(pr) == "Reads usage from transcripts."
    assert _merge_body({"title": "T", "body": "Co-Authored-By: x\n\n🤖 Generated with Claude"}) == "T"


# ---------------------------------------------------------------------------
# Merge gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "asks"),
    [
        ("susanoo land 52 53 --merge", True),
        ("python -m susanoo land #52 --merge --base main", True),
        ("susanoo land 52 53", False),
        ("susanoo land --merge-preview 52", False),
        ("git merge --no-ff feature && susanoo land 52", False),
    ],
)
def test_land_merge_always_asks(command: str, asks: bool, config: dict[str, Any], tmp_path: Path):
    for hands_off in (True, False):
        cfg = json.loads(json.dumps({k: v for k, v in config.items() if not k.startswith("_")}))
        cfg["enforcement"]["hands_off"] = hands_off
        event = {"tool_name": "Bash", "tool_input": {"command": command}}
        decision = pre_tool_use(event, None, tmp_path, cfg)
        assert (decision.permission == "ask") is asks, (command, hands_off)
        if asks:
            assert "say-so" in decision.reason


# ---------------------------------------------------------------------------
# Fixture repositories
# ---------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    # stdin closed: Git for Windows asks "Should I try again? (y/n)" when a scanner holds a file.
    for attempt in range(3):
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
        )
        if proc.returncode == 0 or args[0] not in ("push", "clone", "fetch") or attempt == 2:
            break
        time.sleep(0.5)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


CHECK = """\
import pathlib
import calc

assert calc.VERSION
for path in sorted(pathlib.Path(".").glob("use_*.py")):
    exec(compile(path.read_text(), str(path), "exec"), {})
print("ok")
"""

CHANGELOG = """\
# Changelog

## [Unreleased]

### Added
- Base entry.

## [0.1.0] - 2026-01-01
- First release.
"""

ADR_INDEX = """\
# Decisions

| ADR | Decision |
|---|---|
| [0001](0001-one.md) | One |
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone of a bare `origin` with a Python project whose check runs every use_*.py."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    # Keep pushed objects in packs (fewer files for a virus scanner to hold) and never gc.
    git(origin, "config", "receive.unpackLimit", "1")
    git(origin, "config", "gc.auto", "0")
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        git(work, "config", key, value)
    git(work, "checkout", "-q", "-b", "main")
    (work / "calc.py").write_text("VERSION = 1\n\n\ndef add(a, b):\n    return a + b\n")
    (work / "use_basic.py").write_text("import calc\n\nassert calc.add(1, 1) == 2\n")
    (work / "check.py").write_text(CHECK)
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / "docs" / "decisions").mkdir(parents=True)
    (work / "docs" / "decisions" / "README.md").write_text(ADR_INDEX)
    (work / ".susanoo").mkdir()
    (work / ".susanoo" / "config.toml").write_text(
        "schema_version = 1\n\n[land]\nuse_verification = false\n\n"
        f'[[land.checks]]\nid = "check"\ncommand = [\'{sys.executable}\', "check.py"]\n'
    )
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    git(work, "push", "-q", "origin", "main")
    return work


def branch(work: Path, name: str, files: dict[str, str], *, push: bool = True, start: str = "main") -> str:
    git(work, "checkout", "-q", "-b", name, start)
    for rel, content in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", f"work on {name}")
    if push:
        git(work, "push", "-q", "origin", name)
    git(work, "checkout", "-q", "main")
    return git(work, "rev-parse", name)


def changelog_with(entry: str) -> str:
    return CHANGELOG.replace("- Base entry.\n", f"- Base entry.\n- {entry}\n")


def adr_index_with(row: str) -> str:
    return ADR_INDEX + row + "\n"


def land(work: Path, *args: str) -> tuple[int, str, dict[str, Any]]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["land", *args, "--path", str(work)], stdout=out, stderr=err)
    records = read_records(Path(git(work, "rev-parse", "--path-format=absolute", "--git-common-dir")))
    return code, out.getvalue() + err.getvalue(), records[-1] if records else {}


@pytest.fixture(autouse=True)
def no_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never call the real GitHub CLI from tests."""
    monkeypatch.setattr(land_runner, "gh_available", lambda: False)


def _scratch_left(work: Path) -> list[str]:
    listing = git(work, "worktree", "list", "--porcelain")
    return [line for line in listing.splitlines() if line.startswith("worktree ") and "susanoo-land-" in line]


def test_changelog_only_conflict_is_kept_both_and_tested(repo: Path):
    a = branch(
        repo,
        "feat-a",
        {
            "a.txt": "a\n",
            "CHANGELOG.md": changelog_with("Feature A."),
            "docs/decisions/README.md": adr_index_with("| [0003](0003-c.md) | Three |"),
        },
    )
    branch(
        repo,
        "feat-b",
        {
            "b.txt": "b\n",
            "CHANGELOG.md": changelog_with("Feature B."),
            "docs/decisions/README.md": adr_index_with("| [0002](0002-b.md) | Two |"),
        },
    )
    code, out, record = land(repo, "feat-a", "feat-b")
    assert code == 0, out
    assert record["result"] == "ready"
    assert {c["file"] for c in record["conflicts"]} == {"CHANGELOG.md", "docs/decisions/README.md"}
    assert all(c["resolved"] for c in record["conflicts"])
    assert record["checks_passed"] is True
    assert "ready to land" in out and "susanoo land feat-a feat-b --merge" in out
    assert out.strip().splitlines()[-1].startswith("status: land ready: feat-a feat-b on main@")
    # Already up to date with main: nothing to push for the first candidate.
    assert record["candidates"][0]["pushed"] == a
    assert _scratch_left(repo) == []
    # The combined tree has both entries, base branch first, and the ADR rows in order.
    combined = record["combined_sha"]
    changelog = git(repo, "show", f"{combined}:CHANGELOG.md")
    assert "- Feature A.\n- Feature B." in changelog
    index = git(repo, "show", f"{combined}:docs/decisions/README.md")
    assert index.index("[0002]") < index.index("[0003]")


def test_second_run_reuses_the_passing_result(repo: Path):
    branch(repo, "feat-a", {"a.txt": "a\n"})
    assert land(repo, "feat-a", "--no-push")[0] == 0
    code, out, record = land(repo, "feat-a", "--no-push")
    assert code == 0
    assert record["checks_reused"] and "not rerun" in out
    code, out, record = land(repo, "feat-a", "--no-push", "--no-cache")
    assert code == 0 and not record.get("checks_reused")


def test_pass_alone_fail_combined_is_attributed_to_the_interaction(repo: Path):
    # rename: add() becomes plus(), and its one caller follows. caller: a new caller of add().
    branch(
        repo,
        "rename",
        {
            "calc.py": "VERSION = 1\n\n\ndef plus(a, b):\n    return a + b\n",
            "use_basic.py": "import calc\n\nassert calc.plus(1, 1) == 2\n",
        },
    )
    branch(repo, "caller", {"use_total.py": "import calc\n\nassert calc.add(2, 3) == 5\n"})
    code, out, record = land(repo, "rename", "caller", "--no-push")
    assert code == 1
    assert record["result"] == "failed" and record["checks_passed"] is False
    culprit = record["culprit"]
    assert culprit["candidate"] == "caller"
    assert culprit["kind"] == "interaction"
    assert culprit["with"] == ["rename"]
    assert "AttributeError" in culprit["evidence"][0]["tail"]
    assert "caller passes alone but fails check together with rename" in out
    # Each passes alone: rename is prefix 1, and caller was run alone on the base.
    status = {c["tree"]: c["status"] for c in record["checks"]}
    assert status == {"combined": "failed", "rerun": "failed", "prefix:1": "passed", "alone:2": "passed"}
    assert _scratch_left(repo) == []


def test_branch_broken_alone_is_named(repo: Path):
    branch(repo, "fine", {"a.txt": "a\n"})
    branch(repo, "broken", {"use_bad.py": "raise SystemExit('boom')\n"})
    code, out, record = land(repo, "broken", "fine", "--no-push")
    assert code == 1
    assert record["culprit"]["candidate"] == "broken" and record["culprit"]["kind"] == "alone"


def test_other_conflicts_stop_before_any_check(repo: Path):
    branch(repo, "one", {"calc.py": "VERSION = 2\n\n\ndef add(a, b):\n    return a + b\n"})
    branch(repo, "two", {"calc.py": "VERSION = 3\n\n\ndef add(a, b):\n    return a + b\n"})
    code, out, record = land(repo, "one", "two")
    assert code == 1
    assert record["result"] == "conflict"
    assert record["checks"] == []
    assert record["conflicts"] == [
        {
            "candidate": "two",
            "file": "calc.py",
            "hunks": 1,
            "resolved": False,
            "reason": "not an append-only place",
        }
    ]
    assert "calc.py: needs a manual merge" in out
    assert "needs a manual merge: calc.py (two)" in out.strip().splitlines()[-1]
    assert git(repo, "ls-remote", "origin", "one").split()[0] == git(repo, "rev-parse", "one")
    assert _scratch_left(repo) == []


def test_behind_branch_gets_the_base_merged_in_and_pushed(repo: Path):
    tip = branch(repo, "feat-a", {"a.txt": "a\n", "CHANGELOG.md": changelog_with("Feature A.")})
    # main moves on after the branch was cut, touching the same append-only place.
    (repo / "CHANGELOG.md").write_text(changelog_with("Main entry."))
    git(repo, "commit", "-qam", "main moves")
    git(repo, "push", "-q", "origin", "main")
    code, out, record = land(repo, "feat-a")
    assert code == 0, out
    pushed = record["candidates"][0]["pushed"]
    assert pushed != tip
    assert git(repo, "ls-remote", "origin", "feat-a").split()[0] == pushed
    assert git(repo, "rev-parse", f"{pushed}^1") == tip  # a merge commit on top, not a rebase
    assert git(repo, "rev-parse", f"{pushed}^{{tree}}") == record["combined_tree"]
    assert "local feat-a" in out and "is behind" in out
    assert git(repo, "rev-parse", "feat-a") == tip  # local branches are never moved


def test_diverged_and_merged_branches_are_refused(repo: Path):
    branch(repo, "feat-a", {"a.txt": "a\n"})
    git(repo, "checkout", "-q", "feat-a")
    (repo / "a.txt").write_text("local\n")
    git(repo, "commit", "-qam", "local")
    git(repo, "checkout", "-q", "main")
    other = repo.parent / "other"
    git(repo.parent, "clone", "-q", "-b", "feat-a", str(repo.parent / "origin.git"), str(other))
    git(
        other,
        "-c",
        "user.name=O",
        "-c",
        "user.email=o@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    git(other, "push", "-q", "origin", "feat-a")
    code, out, record = land(repo, "feat-a")
    assert code == 1 and "diverged" in out and record["result"] == "error"
    code, out, _ = land(repo, "main")
    assert code == 1 and "base branch" in out


def test_config_flag_is_refused(repo: Path):
    code, out, _ = land(repo, "feat-a", "--config", "x.toml")
    assert code == 1 and "--config is not accepted" in out


# ---------------------------------------------------------------------------
# --merge against a fake GitHub
# ---------------------------------------------------------------------------


class FakeGitHub:
    """Pull requests on the bare `origin`, merged with `git` the way GitHub's merge button does."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.origin = work.parent / "origin.git"
        self.prs: dict[int, dict[str, Any]] = {}
        self.merges: list[list[str]] = []

    def open(self, number: int, branch: str, title: str) -> None:
        self.prs[number] = {"number": number, "title": title, "body": f"Does {title}.", "headRefName": branch}

    def _view(self, number: int) -> dict[str, Any]:
        pr = self.prs[number]
        head = git(self.origin, "rev-parse", f"refs/heads/{pr['headRefName']}")
        return {
            **pr,
            "headRefOid": head,
            "baseRefName": "main",
            "state": pr.get("state", "OPEN"),
            "isDraft": False,
            "isCrossRepository": False,
            "url": f"https://example.com/pull/{number}",
            "mergeCommit": pr.get("mergeCommit"),
        }

    def gh_json(self, _root: Path, *args: str) -> Any:
        if args[:2] == ("pr", "view"):
            return self._view(int(args[2]))
        if args[:2] == ("pr", "list"):
            branch = args[args.index("--head") + 1]
            return [self._view(n) for n, pr in self.prs.items() if pr["headRefName"] == branch]
        raise AssertionError(args)

    def gh(self, _root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert args[:2] == ("pr", "merge") and "--merge" in args
        number = int(args[2])
        sha = args[args.index("--match-head-commit") + 1]
        subject = args[args.index("--subject") + 1]
        body = args[args.index("--body") + 1]
        pr = self._view(number)
        if pr["headRefOid"] != sha:
            return subprocess.CompletedProcess(args, 1, "", "Head branch was modified")
        tmp = self.work.parent / f"gh-merge-{number}"
        git(self.work.parent, "clone", "-q", "-b", "main", str(self.origin), str(tmp))
        git(tmp, "fetch", "-q", "origin", pr["headRefName"])
        git(
            tmp,
            "-c",
            "user.name=GH",
            "-c",
            "user.email=gh@example.com",
            "merge",
            "-q",
            "--no-ff",
            "-m",
            subject,
            "-m",
            body,
            "FETCH_HEAD",
        )
        git(tmp, "push", "-q", "origin", "main")
        self.prs[number].update(state="MERGED", mergeCommit={"oid": git(tmp, "rev-parse", "HEAD")})
        self.merges.append([subject, sha])
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def github(repo: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub(repo)
    monkeypatch.setattr(land_runner, "gh_available", lambda: True)
    monkeypatch.setattr(land_runner, "gh_json", fake.gh_json)
    monkeypatch.setattr(land_runner, "gh", fake.gh)
    return fake


def test_merge_lands_each_pull_request_on_exactly_the_tested_tree(repo: Path, github: FakeGitHub):
    branch(repo, "feat-a", {"a.txt": "a\n", "CHANGELOG.md": changelog_with("Feature A.")})
    branch(repo, "feat-b", {"b.txt": "b\n", "CHANGELOG.md": changelog_with("Feature B.")})
    github.open(1, "feat-a", "Add feature A")
    github.open(2, "feat-b", "Add feature B")

    code, out, ready = land(repo, "1", "#2")
    assert code == 0 and ready["result"] == "ready", out
    assert github.merges == []  # nothing merges without --merge
    assert "susanoo land 1 2 --merge" in out

    code, out, record = land(repo, "1", "#2", "--merge")
    assert code == 0, out
    assert record["result"] == "merged"
    assert record["checks_reused"] == ready["id"]
    assert [m[0] for m in github.merges] == ["Merge: add feature A (#1)", "Merge: add feature B (#2)"]
    git(repo, "fetch", "-q", "origin")
    assert git(repo, "rev-parse", "origin/main^{tree}") == record["combined_tree"]
    # The second branch got main (with the first merge) merged in, never rebased or forced.
    second = record["candidates"][1]
    assert git(repo, "merge-base", "--is-ancestor", second["tip"], second["pushed"]) == ""
    assert "Generated with" not in git(repo, "log", "-1", "--format=%B", "origin/main")
    assert "status: land merged: #1 #2" in out
    # The state between the two merges was checked too, before anything merged.
    assert {c["tree"] for c in record["checks"]} == {"prefix:1"}
    assert record["passed_trees"] == [record["combined_tree"], record["prefix_trees"][0]]


def test_merge_stops_when_the_base_moved(repo: Path, github: FakeGitHub, monkeypatch: pytest.MonkeyPatch):
    branch(repo, "feat-a", {"a.txt": "a\n"})
    github.open(1, "feat-a", "Add feature A")
    real_fetch_sha = land_runner.Lander._land

    def move_base_then_land(self: land_runner.Lander) -> str:
        (repo / "late.txt").write_text("late\n")
        git(repo, "add", "late.txt")
        git(repo, "commit", "-qm", "late")
        git(repo, "push", "-q", "origin", "main")
        return real_fetch_sha(self)

    monkeypatch.setattr(land_runner.Lander, "_land", move_base_then_land)
    code, out, record = land(repo, "1", "--merge")
    assert code == 1 and "moved" in out and github.merges == []
    assert record["result"] == "error"


def test_merge_needs_pull_requests(repo: Path):
    branch(repo, "feat-a", {"a.txt": "a\n"})
    code, out, _ = land(repo, "feat-a", "--merge")
    assert code == 1 and "no open pull request" in out


# ---------------------------------------------------------------------------
# Records and stats
# ---------------------------------------------------------------------------


def test_records_are_shared_by_worktrees_and_summarized(repo: Path):
    branch(repo, "feat-a", {"a.txt": "a\n"})
    side = repo.parent / "side"
    git(repo, "worktree", "add", "-q", str(side), "feat-a")
    code, _out, record = land(side, "feat-a", "--no-push")
    assert code == 0
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert land_records_path(common).is_file()
    assert record["stages"]["checks"] >= 0 and record["duration_s"] > 0
    summary = stats(read_records(common))
    assert summary["runs"] == 1 and summary["verdict"].startswith("undecided")
    out = io.StringIO()
    assert main(["land", "--stats", "--path", str(repo)], stdout=out) == 0
    assert "land runs: 1" in out.getvalue()


def test_stats_verdicts():
    counter = iter(range(1000))

    def run(kind: str | None = None, resolved: bool = False) -> dict[str, Any]:
        return {
            "kind": "land",
            "combined_tree": f"tree-{next(counter)}",
            "candidates": [{}, {}],
            "result": "ready",
            "duration_s": 30.0,
            "check_seconds": 20.0,
            "conflicts": [{"resolved": resolved}],
            "culprit": {"kind": kind} if kind else None,
        }

    assert stats([run() for _ in range(20)])["verdict"] == "no-go"
    assert stats([run("interaction")] + [run() for _ in range(19)])["verdict"] == "go"
    assert stats([run(resolved=True) for _ in range(5)] + [run() for _ in range(15)])["verdict"] == "go"
    assert stats([run() for _ in range(3)])["verdict"].startswith("undecided")
    # A "ready" run and its --merge rerun of the same combined tree are one landing.
    again = run()
    assert stats([again, dict(again)])["multi_candidate_runs"] == 1


def test_tools_come_from_any_checkout_environment_before_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from susanoo.land.checks import LandSettings, Runner

    scratch, main_checkout, on_path = tmp_path / "scratch", tmp_path / "main", tmp_path / "path"
    sub, name = ("Scripts", "landtool.bat") if sys.platform == "win32" else ("bin", "landtool")
    for folder in (main_checkout / ".venv" / sub, on_path, scratch):
        folder.mkdir(parents=True)
    for folder in (main_checkout / ".venv" / sub, on_path):
        tool = folder / name
        tool.write_text("@echo off\n" if sys.platform == "win32" else "#!/bin/sh\n")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path))
    runner = Runner(LandSettings([], [], [], {}, 60), [scratch, main_checkout])
    found = Path(runner.resolve(["landtool", "{tree}/x"], scratch)[0])
    assert found.parent == main_checkout / ".venv" / sub
    assert runner.resolve(["landtool", "{tree}/x"], scratch)[1] == f"{scratch}/x"


# ---------------------------------------------------------------------------
# Review follow-ups: configuration from the base, compares, flaky checks, a broken base
# ---------------------------------------------------------------------------


def test_keep_both_refuses_blank_base_lines_and_marker_like_content():
    blank_base = _conflict("- a\n", "- b\n", base="\n")
    assert "changed or removed" in resolve_text(blank_base, "CHANGELOG.md", RULES, base_side="ours").reason
    tricky = _conflict("- a\n", "- b\n").replace("- b\n", "- b\n>>>>>>> fake\n", 1)
    assert not resolve_text(tricky, "CHANGELOG.md", RULES, base_side="ours").resolved


def test_sorted_rows_keep_blank_lines_after_the_table():
    text = "| ADR | D |\n|---|---|\n<<<<<<< HEAD\n| [0003](c) | c |\n\n||||||| base\n=======\n| [0002](b) | b |\n>>>>>>> x\n"
    rules = parse_rules([{"path": "x.md", "sort": True}])
    result = resolve_text(text, "x.md", rules, base_side="ours")
    assert result.resolved
    assert (result.text or "").endswith("| [0002](b) | b |\n| [0003](c) | c |\n\n")


def test_cache_key_covers_commands_and_version():
    from susanoo.land.checks import Check, LandSettings

    one = LandSettings([Check("t", ["pytest"], "land")], [], [], {}, 60)
    two = LandSettings([Check("t", ["pytest", "-x"], "land")], [], [], {}, 60)
    assert one.key({}) != two.key({})
    assert one.key({}) == LandSettings([Check("t", ["pytest"], "land")], [], [], {}, 60).key({})


@pytest.mark.parametrize(
    ("tool", "command"),
    [
        ("PowerShell", "susanoo land 52 --merge"),
        ("Bash", 'susanoo "land" 52 "--merge"'),
        ("Bash", "susanoo.exe land 52 \\n  --merge"),
        ("PowerShell", "susanoo land 52 `\n  --merge"),
    ],
)
def test_land_merge_gate_sees_quoted_and_split_commands(
    tool: str, command: str, config: dict[str, Any], tmp_path: Path
):
    event = {"tool_name": tool, "tool_input": {"command": command}}
    assert pre_tool_use(event, None, tmp_path, config).permission == "ask"


def test_a_candidate_cannot_change_its_own_checks(repo: Path):
    weak = 'schema_version = 1\n\n[land]\nuse_verification = false\n\n[[land.checks]]\nid = "check"\n'
    weak += f'command = [\'{sys.executable}\', "-c", "pass"]\n'
    branch(repo, "sneaky", {".susanoo/config.toml": weak, "use_bad.py": "raise SystemExit('boom')\n"})
    code, out, record = land(repo, "sneaky", "--no-push")
    assert code == 1 and record["culprit"]["kind"] == "alone"
    assert record["checks"][0]["command"][-1] == "check.py"  # the base's command, not the branch's


def _add_metric(repo: Path) -> None:
    (repo / "metric.py").write_text(
        'import json, pathlib\nprint(json.dumps({"stats": {"score": float(pathlib.Path("score.txt").read_text())}}))\n'
    )
    (repo / "score.txt").write_text("0.9\n")
    config = (repo / ".susanoo" / "config.toml").read_text()
    config += (
        f'\n[[land.compare]]\nid = "metric"\npaths = ["score.txt"]\ncommand = [\'{sys.executable}\', "metric.py"]\n'
        'no_drop = ["stats.score"]\n'
    )
    (repo / ".susanoo" / "config.toml").write_text(config)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "metric")
    git(repo, "push", "-q", "origin", "main")


def test_compare_fails_when_a_watched_number_drops(repo: Path):
    _add_metric(repo)
    branch(repo, "unrelated", {"a.txt": "a\n"})
    branch(repo, "worse", {"score.txt": "0.8\n"})
    code, out, record = land(repo, "unrelated", "--no-push")
    assert code == 0 and "metric" not in {c["id"] for c in record["checks"]}  # paths not touched
    code, out, record = land(repo, "worse", "--no-push")
    assert code == 1
    metric = next(c for c in record["checks"] if c["id"] == "metric" and c["tree"] == "combined")
    assert metric["status"] == "failed" and "stats.score dropped from 0.9 to 0.8" in metric["note"]


def test_a_check_that_passes_on_rerun_is_recorded_as_flaky(repo: Path, tmp_path: Path):
    marker = (tmp_path / "flaky-ran").as_posix()
    flaky = (
        f"import pathlib\np = pathlib.Path({marker!r})\n"
        "if not p.exists():\n    p.write_text('x')\n    raise SystemExit('first run fails')\n"
    )
    branch(repo, "flaky", {"use_flaky.py": flaky})
    code, out, record = land(repo, "flaky", "--no-push")
    assert code == 0, out
    assert record["flaky"] == ["check"] and record["checks_passed"] is True


def test_a_broken_base_is_not_blamed_on_the_candidate(repo: Path):
    branch(repo, "fine", {"a.txt": "a\n"})
    (repo / "use_broken.py").write_text("raise SystemExit('main is broken')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "break main")
    git(repo, "push", "-q", "origin", "main")
    code, out, record = land(repo, "fine", "--no-push")
    assert code == 1
    assert record["culprit"]["kind"] == "base" and record["culprit"]["candidate"] is None
    assert "fix the base first" in out


def test_merge_refuses_commits_the_pull_request_does_not_show(repo: Path, github: FakeGitHub):
    branch(repo, "feat-a", {"a.txt": "a\n"})
    github.open(1, "feat-a", "Add feature A")
    git(repo, "checkout", "-q", "feat-a")
    (repo / "a.txt").write_text("unpushed\n")
    git(repo, "commit", "-qam", "unpushed")
    git(repo, "checkout", "-q", "main")
    code, out, record = land(repo, "1", "--merge")
    assert code == 1 and "push it first" in out and github.merges == []


def test_merge_checks_the_intermediate_state_even_after_a_cached_pass(repo: Path, github: FakeGitHub):
    # #1 lowers the score and #2 raises it again: the combination passes, the state between fails.
    _add_metric(repo)
    branch(repo, "worse", {"score.txt": "0.8\n"})
    branch(repo, "fix", {"score.txt": "0.95\n"}, start="worse")
    github.open(1, "worse", "Lower the score")
    github.open(2, "fix", "Raise the score")
    code, out, _ready = land(repo, "1", "2")
    assert code == 0, out
    code, out, record = land(repo, "1", "2", "--merge")
    assert code == 1 and github.merges == []
    assert "fails its checks on the way; nothing was merged" in out
    metric = next(c for c in record["checks"] if c["id"] == "metric")
    assert metric["tree"] == "prefix:1" and metric["status"] == "failed"


def test_candidates_adding_merge_drivers_are_refused(repo: Path):
    branch(repo, "union", {".gitattributes": "*.py merge=union\n"})
    code, out, record = land(repo, "union", "--no-push")
    assert code == 1 and "merge= attribute" in out and record["checks"] == []
