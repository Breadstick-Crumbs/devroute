"""`susanoo land`: land agent branches through one tested combined tree (ADR 0014).

1. Fetch the base and the candidates; merge the candidates in order into a scratch worktree at
   the base. Append-only conflicts are resolved by keeping both sides; anything else stops.
2. Run the project's checks on the combined tree (configuration comes from the base).
3. On a failure: rerun once (flaky?), then find the first failing prefix and whether the culprit
   fails alone or only together with the earlier candidates.
4. Merge the base into each candidate, check its tree equals the tested prefix, push.
5. Only with ``--merge`` (given by the user): merge each pull request with ``gh`` against the
   exact pushed head. Without it, stop at "ready to land".
6. Clean up, and record the run.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from susanoo import __version__
from susanoo.config import Config, load_config
from susanoo.land.checks import (
    PLAN_TASK,
    Check,
    CheckResult,
    Compare,
    LandSettings,
    Runner,
    compare_values,
    load_settings,
)
from susanoo.land.gitops import (
    MIN_GIT,
    PR_FIELDS,
    Git,
    LandError,
    common_dir,
    gh,
    gh_available,
    gh_json,
    git_version,
    remove_tree,
    worktrees,
)
from susanoo.land.keepboth import KeepBothRule, Resolution, parse_rules, resolve_text
from susanoo.land.records import DECISION_RULE, append_record, find_pass, read_records

SCRATCH_PREFIX = "susanoo-land-"
ORPHAN_AGE_S = 12 * 3600
DEFAULT_BASE = "main"
_ATTRIBUTION = ("co-authored-by:", "generated with", "🤖")


@dataclass
class LandOptions:
    candidates: list[str]
    base: str | None = None
    remote: str = "origin"
    merge: bool = False
    push: bool = True
    timeout: float = 1800
    reuse: bool = True
    keep_scratch: bool = False


@dataclass
class Candidate:
    spec: str
    branch: str
    pr: dict[str, Any] | None = None
    tip: str = ""
    local: str | None = None
    remote: str | None = None
    worktree: Path | None = None
    pushed: str | None = None
    merged: str | None = None
    conflicted: bool = False

    @property
    def label(self) -> str:
        return f"#{self.pr['number']}" if self.pr else self.branch

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "branch": self.branch,
            "pr": self.pr.get("number") if self.pr else None,
            "tip": self.tip,
            "local": self.local,
            "remote": self.remote,
            "pushed": self.pushed,
            "merged": self.merged,
        }


@dataclass
class _Plan:
    config: Config
    settings: LandSettings
    rules: list[KeepBothRule]
    runner: Runner
    key: str


def _short(sha: str | None) -> str:
    return (sha or "")[:7]


def _merge_subject(pr: dict[str, Any]) -> str:
    """`Merge: <title> (#n)`, the title's first word lowercased unless it is an acronym."""
    title = str(pr.get("title", "")).strip()
    first, _, rest = title.partition(" ")
    if first and not (len(first) > 1 and first[:2].isupper()):
        first = first[0].lower() + first[1:]
    title = f"{first} {rest}".strip()
    return f"Merge: {title} (#{pr['number']})"


def _merge_body(pr: dict[str, Any]) -> str:
    """The first paragraph of the pull request body that is prose, without attribution lines."""
    body = str(pr.get("body") or "")
    for paragraph in body.replace("\r\n", "\n").split("\n\n"):
        lines = [
            line.strip()
            for line in paragraph.splitlines()
            if line.strip() and not line.strip().lower().startswith(_ATTRIBUTION)
        ]
        lines = [line for line in lines if not line.startswith(("#", "- [", "|", "```", "<!--"))]
        if lines:
            return " ".join(lines)
    return str(pr.get("title", "")).strip()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Lander:
    def __init__(self, root: Path, options: LandOptions, say: Callable[[str], None]) -> None:
        self.root = root
        self.options = options
        self.say = say
        self.repo = Git(root)
        self.candidates: list[Candidate] = []
        self.base = options.base or ""
        self.base_sha = ""
        self.prefixes: list[str] = []  # commit after merging each non-conflicted candidate
        self.order: list[Candidate] = []  # the candidates in self.prefixes, in order
        self.scratch: Path | None = None
        self._tree: Path | None = None
        self._git: Git | None = None
        self._plan: _Plan | None = None
        self.base_values: dict[str, dict[str, float]] = {}
        self.compares: list[Compare] = []
        self._worktree_list: list[tuple[Path, str | None]] | None = None
        self.stages: dict[str, float] = {}
        self.record: dict[str, Any] = {
            "kind": "land",
            "id": f"land-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}",
            "version": __version__,
            "rule": DECISION_RULE,
            "started_at": _now(),
            "merge_requested": options.merge,
            "push": options.push,
            "conflicts": [],
            "checks": [],
            "flaky": [],
            "culprit": None,
            "checks_passed": None,
            "warnings": [],
        }
        self.common: Path | None = None

    @property
    def tree(self) -> Path:
        if self._tree is None:
            raise LandError("internal error: no scratch worktree")
        return self._tree

    @property
    def git(self) -> Git:
        if self._git is None:
            raise LandError("internal error: no scratch worktree")
        return self._git

    @property
    def plan(self) -> _Plan:
        if self._plan is None:
            raise LandError("internal error: checks not loaded")
        return self._plan

    # -- driver ----------------------------------------------------------------------------

    def run(self) -> tuple[dict[str, Any], int]:
        started = time.monotonic()
        code = 1
        try:
            code = self._run()
        except LandError as exc:
            self.record["result"] = "error"
            self.record["error"] = str(exc)
            self.say(f"stopped: {exc}")
        finally:
            self._cleanup()
            self.record["duration_s"] = round(time.monotonic() - started, 2)
            self.record["check_seconds"] = round(self._plan.runner.seconds, 2) if self._plan else 0.0
            self.record["stages"] = {k: round(v, 2) for k, v in self.stages.items()}
            self.record["candidates"] = [c.to_dict() for c in self.candidates]
            if self.common is not None:
                try:
                    append_record(self.common, self.record)
                except OSError as exc:
                    self.say(f"warning: could not write the land record: {exc}")
        return self.record, code

    def _stage(self, name: str, started: float) -> None:
        self.stages[name] = self.stages.get(name, 0.0) + time.monotonic() - started

    def _run(self) -> int:
        t = time.monotonic()
        self._preflight()
        self._resolve()
        self._stage("resolve", t)

        t = time.monotonic()
        self._make_scratch()
        self._load_plan()
        if not self._combine():
            self._stage("combine", t)
            self.record["result"] = "conflict"
            self._status("conflict", "conflicts need a manual merge")
            return 1
        self._stage("combine", t)

        t = time.monotonic()
        passed = self._check()
        self._stage("checks", t)
        if not passed:
            self.record["result"] = "failed"
            culprit = self.record.get("culprit") or {}
            self._status("failed", self._culprit_text(culprit))
            return 1

        t = time.monotonic()
        result = self._land()
        self._stage("land", t)
        self.record["result"] = result
        return 0

    # -- 0. preflight and candidates -------------------------------------------------------

    def _preflight(self) -> None:
        if not self.options.candidates:
            raise LandError("name at least one pull request number or branch")
        version = git_version()
        if version < MIN_GIT:
            raise LandError(
                f"git {MIN_GIT[0]}.{MIN_GIT[1]} or later is needed (found {version[0]}.{version[1]})"
            )
        try:
            self.common = common_dir(self.root)
        except LandError as exc:
            raise LandError(f"{self.root} is not inside a git repository") from exc

    def _pr_view(self, number: str) -> dict[str, Any]:
        if not gh_available():
            raise LandError(f"pull request {number} needs the GitHub CLI `gh`, which was not found")
        data = gh_json(self.root, "pr", "view", number, "--json", PR_FIELDS)
        if not isinstance(data, dict):
            raise LandError(f"gh returned no data for pull request {number}")
        return data

    def _resolve(self) -> None:
        seen: set[str] = set()
        for spec in self.options.candidates:
            value = spec.strip().lstrip("#")
            if value.isdigit():
                found_pr = self._pr_view(value)
                cand = Candidate(spec, str(found_pr["headRefName"]), found_pr)
            else:
                cand = Candidate(spec, spec.strip())
                if gh_available():
                    try:
                        found = gh_json(
                            self.root,
                            "pr",
                            "list",
                            "--head",
                            cand.branch,
                            "--state",
                            "open",
                            "--json",
                            PR_FIELDS,
                        )
                    except LandError as exc:
                        if self.options.merge:
                            raise
                        self._warn(f"could not look up a pull request for {cand.branch} ({exc})")
                        found = []
                    if isinstance(found, list) and len(found) > 1:
                        raise LandError(f"branch {cand.branch} has {len(found)} open pull requests; name one")
                    cand.pr = found[0] if isinstance(found, list) and found else None
            if cand.branch in seen:
                raise LandError(f"{cand.branch} is named twice")
            seen.add(cand.branch)
            self.candidates.append(cand)

        prs = [c.pr for c in self.candidates if c.pr]
        self.base = self.options.base or (str(prs[0]["baseRefName"]) if prs else DEFAULT_BASE)
        for cand in self.candidates:
            info = cand.pr
            if cand.branch == self.base:
                raise LandError(f"{cand.branch} is the base branch")
            if info is None:
                if self.options.merge:
                    raise LandError(f"{cand.branch} has no open pull request; --merge needs one")
                continue
            if info.get("isCrossRepository"):
                raise LandError(
                    f"{cand.label} comes from a fork; land handles same-repository pull requests only"
                )
            if info.get("state") != "OPEN":
                raise LandError(f"{cand.label} is {str(info.get('state')).lower()}, not open")
            if info.get("baseRefName") != self.base:
                raise LandError(f"{cand.label} targets {info.get('baseRefName')}, not {self.base}")
            if info.get("isDraft") and self.options.merge:
                raise LandError(f"{cand.label} is a draft; mark it ready first")

        remote = self.options.remote
        self.repo.run(
            "fetch", "--no-tags", remote, f"+refs/heads/{self.base}:refs/remotes/{remote}/{self.base}"
        )
        base_sha = self.repo.sha(f"refs/remotes/{remote}/{self.base}")
        if base_sha is None:
            raise LandError(f"{remote}/{self.base} was not found")
        self.base_sha = base_sha
        checked_out = {branch: path for path, branch in self._worktrees() if branch}
        for cand in self.candidates:
            fetched = self.repo.run(
                "fetch",
                "--no-tags",
                remote,
                f"+refs/heads/{cand.branch}:refs/remotes/{remote}/{cand.branch}",
                check=False,
            )
            if fetched.returncode != 0 and cand.pr:
                raise LandError(f"could not fetch {cand.branch} for {cand.label}")
            cand.local = self.repo.sha(f"refs/heads/{cand.branch}")
            cand.remote = (
                self.repo.sha(f"refs/remotes/{remote}/{cand.branch}") if fetched.returncode == 0 else None
            )
            cand.tip = self._pick_tip(cand)
            if self.repo.is_ancestor(cand.tip, self.base_sha):
                raise LandError(f"{cand.label} is already in {self.base}")
            cand.worktree = checked_out.get(cand.branch)
            if cand.worktree is not None and cand.local == cand.tip:
                dirty = Git(cand.worktree).run("status", "--porcelain", "--untracked-files=no", check=False)
                if dirty.stdout.strip():
                    self._warn(
                        f"{cand.label}: uncommitted changes in {cand.worktree} are not part of this landing"
                    )
        names = ", ".join(f"{c.label} ({c.branch} {_short(c.tip)})" for c in self.candidates)
        self.say(f"land: {remote}/{self.base} {_short(self.base_sha)} + {names}")
        self.say("note: the checks run the candidates' code on this machine")
        self.record["base"] = {"branch": self.base, "remote": remote, "sha": self.base_sha}

    def _pick_tip(self, cand: Candidate) -> str:
        local, remote = cand.local, cand.remote
        if local and remote:
            if local == remote or self.repo.is_ancestor(local, remote):
                return remote
            if self.repo.is_ancestor(remote, local):
                return local
            raise LandError(
                f"{cand.branch}: the local branch and {self.options.remote}/{cand.branch} have diverged"
            )
        if local or remote:
            return str(local or remote)
        raise LandError(f"no branch named {cand.branch}, locally or on {self.options.remote}")

    def _worktrees(self) -> list[tuple[Path, str | None]]:
        if self._worktree_list is None:
            self._worktree_list = worktrees(self.root)
        return self._worktree_list

    def _warn(self, text: str) -> None:
        self.record["warnings"].append(text)
        self.say(f"warning: {text}")

    # -- 1. scratch worktree and combination -----------------------------------------------

    def _make_scratch(self) -> None:
        self._prune_orphans()
        self.scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX))
        hooks = self.scratch / "hooks"
        hooks.mkdir()
        (self.scratch / "land.json").write_text(
            json.dumps({"id": self.record["id"], "pid": os.getpid(), "repo": str(self.root)}),
            encoding="utf-8",
        )
        self._tree = self.scratch / "tree"
        Git(self.root, hooks_path=hooks).run("worktree", "add", "--detach", str(self._tree), self.base_sha)
        self._git = Git(self._tree, hooks_path=hooks, conflict_style=True)

    def _prune_orphans(self) -> None:
        """Remove scratch worktrees that a killed run left behind (older than 12 hours)."""
        for path, _branch in self._worktrees():
            parent = path.parent
            if path.name != "tree" or not parent.name.startswith(SCRATCH_PREFIX):
                continue
            marker = parent / "land.json"
            try:
                age = time.time() - marker.stat().st_mtime
            except OSError:
                continue
            if age > ORPHAN_AGE_S:
                self.repo.run("worktree", "remove", "--force", str(path), check=False)
                remove_tree(parent)
        self.repo.run("worktree", "prune", check=False)

    def _load_plan(self) -> None:
        """Configuration, registry, and profiles from the base's tree, never from a candidate."""
        from susanoo.pipeline import plan_task
        from susanoo.registry import load_registry
        from susanoo.router import load_profiles

        config = load_config(self.tree)
        main = next((p for p, _b in self._worktrees()), None)
        tool_roots = list(dict.fromkeys([self.root, *([main] if main else [])]))
        steps: list[Any] = []
        if config.get("land", {}).get("use_verification", True) is not False:
            # Discovery looks at the main worktree, where the project's environment usually lives.
            plan = plan_task(
                PLAN_TASK,
                tool_roots[-1],
                config,
                override="deep",
                registry=load_registry(self.tree),
                profiles=load_profiles(self.tree),
            )
            steps = plan.verification.steps
        settings = load_settings(config, steps, self.options.timeout)
        runner = Runner(settings, tool_roots)
        rules = parse_rules(settings.keep_both)
        self._plan = _Plan(config, settings, rules, runner, settings.key(runner.tools(self.tree)))
        for step_id in settings.skipped:
            self._warn(
                f"required check {step_id} has no tool Susanoo can run; it is not part of this landing"
            )

    def _merge(self, git: Git, ref: str, message: str, label: str, *, base_side: str) -> bool:
        """Merge ``ref`` into the scratch HEAD, keeping both sides of append-only conflicts.

        Returns False (with the merge aborted) when a conflict needs a person.
        """
        proc = git.run("merge", "--no-ff", "--no-edit", "-m", message, ref, check=False)
        if proc.returncode == 0:
            return True
        files = git.conflicted_files()
        if not files:
            git.run("merge", "--abort", check=False)
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            raise LandError(f"merging {label} failed: {detail[-1] if detail else proc.returncode}")
        results: list[Resolution] = []
        for name in files:
            path = git.cwd / name
            try:
                text = path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                results.append(Resolution(name, reason="not a UTF-8 text file"))
                continue
            results.append(resolve_text(text, name, self.plan.rules, base_side=base_side))
        for res in results:
            self.record["conflicts"].append({"candidate": label, **res.to_dict()})
        if not all(r.resolved for r in results):
            git.run("merge", "--abort", check=False)
            for res in results:
                state = "kept both" if res.resolved else f"needs a manual merge ({res.reason})"
                self.say(f"  conflict {label}: {res.path}: {state}")
            return False
        for res in results:
            (git.cwd / res.path).write_bytes((res.text or "").encode("utf-8"))
        git.run("add", "--", *[r.path for r in results])
        git.run("commit", "--no-edit")
        for res in results:
            self.say(f"  conflict {label}: {res.path}: kept both ({res.hunks} hunk{'s' * (res.hunks != 1)})")
        return True

    def _combine(self) -> bool:
        ok = True
        for cand in self.candidates:
            message = f"land: merge {cand.label} ({cand.branch})"
            if self._merge(self.git, cand.tip, message, cand.label, base_side="ours"):
                self.prefixes.append(self.git.out("rev-parse", "HEAD"))
                self.order.append(cand)
                self.say(f"merged {cand.label}")
            else:
                cand.conflicted = True
                ok = False
        if not ok:
            return False
        self.record["combined_sha"] = self.prefixes[-1]
        self.record["combined_tree"] = self.git.tree(self.prefixes[-1])
        self.record["base_tree"] = self.git.tree(self.base_sha)
        self.record["prefix_trees"] = [self.git.tree(p) for p in self.prefixes]
        return True

    # -- 2. checks ---------------------------------------------------------------------------

    def _checkout(self, ref: str) -> CheckResult | None:
        self.git.run("checkout", "--detach", "--force", ref)
        self.git.run("clean", "-fdxq", check=False)
        return self.plan.runner.setup(self.tree)

    def _check(self) -> bool:
        plan = self.plan
        combined = self.prefixes[-1]
        self.record["checks_key"] = plan.key
        if self.options.reuse:
            hit = find_pass(
                read_records(self.common) if self.common else [],
                self.record["combined_tree"],
                self.record["base_tree"],
                plan.key,
            )
            if hit is not None:
                self.record["checks_passed"] = True
                self.record["checks_reused"] = hit.get("id")
                self.say(f"checks: passed on this exact tree in {hit.get('id')}; not rerun")
                return True
        changed = self.git.changed_files(self.base_sha, combined)
        compares = [c for c in plan.settings.compares if c.applies(changed)]
        self.compares = compares
        if not plan.settings.checks and not compares:
            raise LandError(
                "no checks to run on the combined tree; add [[land.checks]] to the project configuration"
            )

        if compares:
            failed_setup = self._checkout(self.base_sha)
            if failed_setup is not None:
                raise LandError(f"setup failed on the base: {failed_setup.output_tail.strip()[-300:]}")
            for compare in compares:
                values, _duration, error = plan.runner.measure(compare, self.tree)
                if values is None:
                    raise LandError(f"{compare.id} failed on the base ({error}); fix the base first")
                self.base_values[compare.id] = values

        failed_setup = self._checkout(combined)
        if failed_setup is not None:
            failed_setup.tree = "combined"
            self._record_check(failed_setup)
            self.record["checks_passed"] = False
            self.record["culprit"] = {"kind": "setup", "candidate": None, "checks": [failed_setup.id]}
            return False
        self.say(f"checks on the combined tree {_short(combined)}:")
        failing: list[str] = []
        for check in plan.settings.checks:
            result = plan.runner.run(check, self.tree, "combined")
            self._record_check(result)
            if not result.ok:
                failing.append(check.id)
        for compare in compares:
            result = self._compare(compare, "combined")
            self._record_check(result)
            if not result.ok:
                failing.append(compare.id)
        if failing:
            self.say("rerunning the failing checks once")
            still: list[str] = []
            for result in self._run_ids(failing, "rerun"):
                self._record_check(result)
                if not result.ok:
                    still.append(result.id)
            self.record["flaky"] = [i for i in failing if i not in still]
            failing = still
        if failing:
            self.record["checks_passed"] = False
            t = time.monotonic()
            self._attribute(failing)
            self._stage("attribute", t)
            return False
        self.record["checks_passed"] = True
        return True

    def _record_check(self, result: CheckResult) -> None:
        data = result.to_dict()
        if result.ok:
            data["output_tail"] = ""
        self.record["checks"].append(data)
        if result.tree in ("combined", "rerun"):
            how = result.note or " ".join(result.command)
            self.say(f"  {result.id:<18} {result.status:<7} {result.duration_s:>7.1f}s  {how}")

    def _compare(self, compare: Compare, label: str) -> CheckResult:
        values, duration, error = self.plan.runner.measure(compare, self.tree)
        if values is None:
            return CheckResult(compare.id, "error", compare.command, round(duration, 2), label, note=error)
        base = self.base_values[compare.id]
        problems = compare_values(compare, base, values)
        summary = ", ".join(f"{k} {values[k]:g} (base {base[k]:g})" for k in values)
        status = "failed" if problems else "passed"
        note = "; ".join(problems) if problems else summary
        return CheckResult(compare.id, status, compare.command, round(duration, 2), label, note=note)

    def _run_ids(self, ids: list[str], label: str) -> list[CheckResult]:
        results: list[CheckResult] = []
        checks: dict[str, Check] = {c.id: c for c in self.plan.settings.checks}
        compares = {c.id: c for c in self.compares}
        for check_id in ids:
            if check_id in checks:
                results.append(self.plan.runner.run(checks[check_id], self.tree, label))
            elif check_id in compares:
                results.append(self._compare(compares[check_id], label))
        return results

    def _fails(self, ref: str, ids: list[str], label: str) -> bool:
        setup = self._checkout(ref)
        if setup is not None:
            setup.tree = label
            self._record_check(setup)
            return True
        results = self._run_ids(ids, label)
        for result in results:
            self._record_check(result)
        return any(not r.ok for r in results)

    # -- 3. attribution ----------------------------------------------------------------------

    def _attribute(self, ids: list[str]) -> None:
        culprit: dict[str, Any] = {"checks": ids}
        n = len(self.prefixes)
        k = n
        if n > 1:
            self.say(f"finding which candidate breaks {', '.join(ids)}")
            for i in range(n - 1):
                if self._fails(self.prefixes[i], ids, f"prefix:{i + 1}"):
                    k = i + 1
                    break
        cand = self.order[k - 1]
        culprit["candidate"] = cand.label
        if k == 1:
            if self._fails(self.base_sha, ids, "base"):
                culprit.update(kind="base", candidate=None)
            else:
                culprit["kind"] = "alone"
        else:
            self.git.run("checkout", "--detach", "--force", self.base_sha)
            if not self._merge(self.git, cand.tip, f"land: {cand.label} alone", cand.label, base_side="ours"):
                culprit["kind"] = "unknown"
            elif self._fails("HEAD", ids, f"alone:{k}"):
                culprit["kind"] = "alone"
            else:
                culprit["kind"] = "interaction"
                culprit["with"] = [c.label for c in self.order[: k - 1]]
        failing = [c for c in self.record["checks"] if c["status"] != "passed" and c["tree"] == "rerun"]
        culprit["evidence"] = [{"id": c["id"], "tail": c["output_tail"][-600:] or c["note"]} for c in failing]
        self.record["culprit"] = culprit
        self.say(self._culprit_text(culprit))
        for item in culprit["evidence"]:
            self.say(f"  {item['id']}: " + item["tail"].strip().replace("\n", "\n    ")[-600:])

    @staticmethod
    def _culprit_text(culprit: dict[str, Any]) -> str:
        kind = culprit.get("kind")
        checks = ", ".join(culprit.get("checks", []))
        who = culprit.get("candidate")
        if kind == "base":
            return f"the base fails {checks} on its own; fix the base first"
        if kind == "setup":
            return "setup failed on the combined tree"
        if kind == "alone":
            return f"{who} fails {checks} on its own (on the base)"
        if kind == "interaction":
            return f"{who} passes alone but fails {checks} together with {', '.join(culprit.get('with', []))}"
        return f"{who} breaks {checks} (could not test it alone)"

    # -- 4 and 5. update, push, merge ----------------------------------------------------------

    def _land(self) -> str:
        remote = self.options.remote
        if not self.options.push and not self.options.merge:
            self._ready()
            return "ready"
        expected = self.base_sha
        for i, cand in enumerate(self.order):
            if i > 0 and not self.options.merge:
                break
            if self.options.merge:
                self.repo.run(
                    "fetch", "--no-tags", remote, f"+refs/heads/{self.base}:refs/remotes/{remote}/{self.base}"
                )
                current = self.repo.sha(f"refs/remotes/{remote}/{self.base}")
                if current != expected:
                    raise LandError(
                        f"{remote}/{self.base} moved to {_short(current)} (expected {_short(expected)}); rerun land"
                    )
            updated = self._update(cand, expected, i)
            self._push(cand, updated)
            if self.options.merge:
                expected = self._merge_pr(cand, updated, i)
        if self.options.merge:
            self._status("merged", f"{self.base} is at {_short(expected)}, the tested tree")
            return "merged"
        self._ready()
        return "ready"

    def _update(self, cand: Candidate, base_sha: str, i: int) -> str:
        """The candidate with the base merged in; its tree must equal the tested prefix."""
        if self.repo.is_ancestor(base_sha, cand.tip):
            updated = cand.tip
        else:
            # A plain merge in the user's name: this commit is pushed, so repository hooks run.
            git = Git(self.git.cwd, conflict_style=True)
            git.run("checkout", "--detach", "--force", cand.tip)
            message = f"Merge branch '{self.base}' into {cand.branch}"
            if not self._merge(git, base_sha, message, cand.label, base_side="theirs"):
                raise LandError(f"merging {self.base} into {cand.label} conflicts in a way the check did not")
            updated = git.out("rev-parse", "HEAD")
        tree = self.git.tree(updated)
        if tree != self.record["prefix_trees"][i]:
            raise LandError(
                f"{cand.label} with {self.base} merged in differs from the tested tree; nothing was pushed for it"
            )
        return updated

    def _push(self, cand: Candidate, updated: str) -> None:
        remote = self.options.remote
        if cand.remote == updated:
            cand.pushed = updated
            return
        if not self.options.push:
            raise LandError(f"{cand.label} needs a push to land; drop --no-push")
        # A normal push: it fails instead of overwriting if the remote branch moved.
        self.repo.run("push", remote, f"{updated}:refs/heads/{cand.branch}")
        cand.pushed = updated
        self.say(f"pushed {cand.label}: {cand.branch} {_short(cand.remote)} -> {_short(updated)}")
        if cand.local and cand.local != updated:
            where = f" (checked out in {cand.worktree})" if cand.worktree else ""
            self.say(
                f"  local {cand.branch}{where} is behind; update it with: git merge --ff-only {remote}/{cand.branch}"
            )

    def _merge_pr(self, cand: Candidate, updated: str, i: int) -> str:
        if cand.pr is None:
            raise LandError(f"{cand.label} has no pull request to merge")
        number = str(cand.pr["number"])
        gh(
            self.root,
            "pr",
            "merge",
            number,
            "--merge",
            "--match-head-commit",
            updated,
            "--subject",
            _merge_subject(cand.pr),
            "--body",
            _merge_body(cand.pr),
        )
        view = gh_json(self.root, "pr", "view", number, "--json", "state,mergeCommit")
        merged = (view.get("mergeCommit") or {}).get("oid") if isinstance(view, dict) else None
        if not isinstance(view, dict) or view.get("state") != "MERGED" or not merged:
            raise LandError(f"{cand.label} did not report as merged; check it on GitHub")
        remote = self.options.remote
        self.repo.run(
            "fetch", "--no-tags", remote, f"+refs/heads/{self.base}:refs/remotes/{remote}/{self.base}"
        )
        if self.repo.sha(f"refs/remotes/{remote}/{self.base}") != merged:
            raise LandError(f"{remote}/{self.base} is not at the merge commit of {cand.label}; stopping")
        if self.git.tree(merged) != self.record["prefix_trees"][i]:
            raise LandError(f"the merge of {cand.label} produced a tree that was not tested; stopping")
        cand.merged = merged
        self.say(f"merged {cand.label} into {self.base}: {_short(merged)}")
        return str(merged)

    def _ready(self) -> None:
        specs = " ".join(c.spec for c in self.order)
        base = f" --base {self.base}" if self.options.base else ""
        remote = f" --remote {self.options.remote}" if self.options.remote != "origin" else ""
        self.say("ready to land. To merge (the user runs or approves this):")
        self.say(f"  susanoo land {specs}{base}{remote} --merge")
        first = self.order[0]
        if first.pr and first.pushed:
            self.say(
                f"  first step: gh pr merge {first.pr['number']} --merge --match-head-commit {first.pushed}"
                f' --subject "{_merge_subject(first.pr)}"'
            )
        self._status("ready", "waiting for the user's --merge")

    def _status(self, result: str, detail: str) -> None:
        labels = " ".join(c.label for c in self.candidates)
        checks = [c for c in self.record["checks"] if c["tree"] == "combined"]
        passed = sum(1 for c in checks if c["status"] == "passed")
        kept = sum(1 for c in self.record["conflicts"] if c["resolved"])
        parts = [f"land {result}: {labels} on {self.base}@{_short(self.base_sha)}"]
        if self.record.get("checks_reused"):
            parts.append(
                f"combined {_short(self.record['combined_sha'])} passed in {self.record['checks_reused']}"
            )
        elif self.record.get("combined_sha") and checks:
            parts.append(f"combined {_short(self.record['combined_sha'])} {passed}/{len(checks)} checks")
        if kept:
            parts.append(f"{kept} conflict{'s' * (kept != 1)} kept both")
        if self.record["flaky"]:
            parts.append("flaky: " + ", ".join(self.record["flaky"]))
        self.say("status: " + "; ".join(parts) + f"; {detail} ({self.record['id']})")

    # -- 6. cleanup --------------------------------------------------------------------------

    def _cleanup(self) -> None:
        if self.scratch is None:
            return
        if self.options.keep_scratch:
            self.say(f"scratch worktree kept at {self._tree}")
            return
        if self._tree is not None:
            self.repo.run("worktree", "remove", "--force", str(self._tree), check=False)
        if not remove_tree(self.scratch):
            self._warn(f"could not delete {self.scratch}; delete it later")
        self.repo.run("worktree", "prune", check=False)


def land(root: Path, options: LandOptions, say: Callable[[str], None]) -> tuple[dict[str, Any], int]:
    return Lander(root, options, say).run()
