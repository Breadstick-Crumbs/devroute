"""Live check of automatic routing (`--auto`) against a real headless Claude Code session.

Like scripts/live_enforcement_check.py, this is a manual check that uses your Claude account. Each
check runs in a throwaway project installed with
`susanoo init --enforce --enforce-scope settings --auto`, so your own ~/.claude is never touched.
None of the prompts mention Susanoo.

    python scripts/live_auto_check.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_enforcement_check as live

TASK = "Add a function hello() to app.py that returns the string 'hi'"
QUESTION = "What does app.py do? Answer in one sentence."
# Phrased as a problem, not a task: the hook does not route it, so Claude has to.
UNROUTED = "app.py is missing the hello() function that should return the string 'hi'"


def project(base: Path, name: str) -> Path:
    root = live.make_project(base, name, autonomy_off=False)
    live.susanoo(
        "init",
        "--enforce",
        "--enforce-scope",
        "settings",
        "--auto",
        "--no-agents",
        "--path",
        str(root),
        cwd=root,
    )
    return root


def check_question(base: Path, args: argparse.Namespace) -> live.Result:
    res = live.Result("question: not routed", "INCONCLUSIVE")
    root = project(base, "question")
    results = live.session([QUESTION], root, "default", args)
    if not results or results[-1].get("is_error"):
        return live.Result(
            res.check, "ERROR", [str(results[-1].get("result") if results else "no output")[:200]]
        )
    routes = live.records(root, "routes")
    res.details.append(f"routes recorded: {len(routes)}")
    res.verdict = "PASS" if not routes else "FAIL"
    return res


def check_default(base: Path, args: argparse.Namespace) -> live.Result:
    res = live.Result("default mode: task routed without /susanoo; edits wait for the plan", "INCONCLUSIVE")
    root = project(base, "default")
    results = live.session([TASK, live.EDIT_NOW], root, "default", args)
    if len(results) < 2 or results[-1].get("is_error"):
        return live.Result(
            res.check, "ERROR", [str(results[-1].get("result") if results else "no output")[:200]]
        )
    routes = [r for r in live.records(root, "routes") if r.get("auto")]
    denies = live.denies(root)
    changed = (root / "app.py").read_text(encoding="utf-8") != live.APP
    res.details.append(
        f"auto routes: {len(routes)}; hook denials: {denies}; app.py changed: {'YES' if changed else 'no'}"
    )
    if not routes:
        res.verdict = "FAIL"
        res.details.append("the prompt was not routed")
    elif changed:
        res.verdict = "FAIL"
    elif denies:
        res.verdict = "PASS"
    else:
        res.details.append("routed, but Claude did not attempt an edit, so the gate was not exercised")
    return res


def check_autonomous(base: Path, args: argparse.Namespace) -> live.Result:
    res = live.Result(
        "autonomous mode (acceptEdits): routed, built without waiting, verified", "INCONCLUSIVE"
    )
    root = project(base, "autonomous")
    results = live.session([TASK], root, "acceptEdits", args)
    if not results or results[-1].get("is_error"):
        return live.Result(
            res.check, "ERROR", [str(results[-1].get("result") if results else "no output")[:200]]
        )
    routes = [r for r in live.records(root, "routes") if r.get("auto")]
    changed = (root / "app.py").read_text(encoding="utf-8") != live.APP
    asks = [e for e in live.records(root, "enforcement") if e.get("permission") in ("ask", "deny")]
    evidence = live.records(root, "evidence")
    blocks = [e for e in live.records(root, "enforcement") if e.get("blocked_stop")]
    res.details.append(
        f"auto routes: {len(routes)}; app.py changed: {'yes' if changed else 'NO'}; Susanoo asks/denies: {len(asks)}; "
        f"evidence records: {len(evidence)}; stop blocks: {len(blocks)}"
    )
    if not routes or not changed or asks:
        res.verdict = "FAIL"
    elif evidence or blocks:
        res.verdict = "PASS"
    else:
        res.details.append("built without asking, but no verification evidence or stop block was recorded")
    return res


def check_unrouted(base: Path, args: argparse.Namespace) -> live.Result:
    res = live.Result("unrouted problem report: Claude routes it itself, then builds", "INCONCLUSIVE")
    root = project(base, "unrouted")
    results = live.session([UNROUTED], root, "bypassPermissions", args)
    if not results or results[-1].get("is_error"):
        return live.Result(
            res.check, "ERROR", [str(results[-1].get("result") if results else "no output")[:200]]
        )
    routes = live.records(root, "routes")
    agent = [r for r in routes if r.get("routed_by") == "agent"]
    changed = (root / "app.py").read_text(encoding="utf-8") != live.APP
    res.details.append(
        f"routes: {len(routes)}; routed by Claude: {len(agent)}; app.py changed: {'yes' if changed else 'no'}"
    )
    if agent:
        res.details.append(f"profile {agent[0].get('profile')}, task {str(agent[0].get('task'))[:80]!r}")
    res.verdict = "PASS" if agent and changed else "FAIL"
    return res


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check automatic routing in real headless Claude Code sessions."
    )
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--budget", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--only", choices=["question", "default", "autonomous", "unrouted"])
    args = parser.parse_args()
    if not args.claude:
        print("claude CLI not found on PATH", file=sys.stderr)
        return 2
    live.SCOPE = "settings"
    checks = {
        "question": check_question,
        "default": check_default,
        "autonomous": check_autonomous,
        "unrouted": check_unrouted,
    }
    results: list[live.Result] = []
    print("Susanoo live auto-routing check (results print as each check finishes)")
    print("=" * 32, flush=True)
    with tempfile.TemporaryDirectory(prefix="susanoo-auto-", ignore_cleanup_errors=True) as tmp:
        for name, check in checks.items():
            if args.only in (None, name):
                try:
                    results.append(check(Path(tmp), args))
                except Exception as exc:  # keep going so one broken check doesn't hide the rest
                    results.append(live.Result(f"{name}: check crashed", "ERROR", [repr(exc)[:300]]))
                live.report(results[-1])
    return 1 if any(r.verdict == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
