# 0016: Route records across a repository's checkouts

**Status:** accepted (Unreleased)

## Context

Records are kept per checkout: in project mode in `<checkout>/.susanoo/records/`, in global mode in `~/.susanoo/projects/<name>-<hash of the path>/`. A git worktree is a separate checkout, so it has its own records.

The DEEP workflow tells Claude to work on a branch or in a worktree. The route is usually recorded where the session started, in the main checkout, while the code changes and the checks happen in the worktree. `susanoo verify --execute` run in the worktree could not find the route ("no route records yet"). Run in the main checkout instead, it would have tested the unchanged code there. So the only honest option was to report the worktree's results with `susanoo evidence`, which the evidence gate does not accept for checks Susanoo can run itself (ADR 0007), and the outcome had to be `partial` although every check had passed.

The fix must not let reported evidence stand in for executed evidence, and executed evidence must come from runs on the code being verified.

## Options

1. **One record store per repository**, keyed by git's common directory, shared by all checkouts.
2. **Look up a route in the repository's other checkouts** when it is not in this one, and record the evidence next to the route.
3. **An explicit flag** (`--records-from <checkout>`) that names where the route is.
4. **Accept reported evidence** from worktrees in the gate.

## Decision

Option 2. `susanoo verify`, `susanoo evidence`, and `susanoo outcome` look the route up in the current checkout's records and in the other checkouts of the same git repository:

- **Which checkouts:** the main working tree and the linked worktrees, found by reading `.git` files, `commondir`, and `<common dir>/worktrees/*/gitdir`. Git is not run. A folder counts only when the repository lists it (as its main working tree or a registered worktree) and its own `.git` leads back to the repository. The current folder must be listed too. So a stale or edited worktree entry cannot name an unrelated folder, a hand-written `.git` file in an unregistered folder does not connect it, and routes of another repository are never found.
- **Which route:** an id or prefix is looked up in the other checkouts only when it matches nothing here; a match in more than one checkout is an error. With a Claude Code session, `latest` is the session's newest own route in any checkout (this checkout's wins a tie), so a route the hook recorded in the main checkout after an earlier one in the worktree is the one that gets the evidence, and another session's route is never taken from elsewhere. Without a session, `latest` stays local and the error names the checkouts that do have routes.
- **Where evidence goes:** next to the route, so the evidence gate and the Stop hook in the route's checkout see it. The commands still run in the current checkout, on its code, planned from its own configuration and tools.
- **What the evidence says:** evidence recorded for a route in another checkout carries `checkout`, the folder the commands ran in, and `commit`, the commit checked out there (read from `HEAD` and the refs, without running git; left out when it cannot be read). Uncommitted changes are not part of `commit`. Evidence recorded in the route's own checkout is unchanged.
- **How the gate counts it:** the latest executed run of a step counts per checkout, and the step passes only when it passes in every checkout that ran it. A pass in a clean checkout therefore never clears a failure in the checkout with the change; only a later pass there does.

`susanoo approve` stays local: approvals are the user's confirmation and are not widened by this change.

Option 1 would change where every existing record lives and let all checkouts share `latest`, open routes, and the enforcement state, which concurrent sessions in different worktrees rely on being separate. Option 3 makes the agent remember a flag the brief does not mention, and run without it, the command fails as before. Option 4 is what the gate exists to prevent.

## Consequences

- DEEP work in a worktree can record executed evidence and a `completed` outcome without extra flags.
- Susanoo still cannot tell which checkout holds the change a route is about. A checkout that only ever passed (for example an unchanged one) counts like the route's own checkout did before; `checkout` and `commit` make such evidence visible in the records. A failure is cleared only by a pass in the same checkout.
- A failing run in a worktree that was removed afterwards keeps that step failing; rerun the checks where the change now is, record `partial`, or let the user accept the missing evidence.
- `latest` with a session reads the other checkouts' route records on every call; an id or prefix reads them only when it is not found here.
