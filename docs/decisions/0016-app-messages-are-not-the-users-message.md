# 0016: Messages the app sends are not the user's message

**Status:** accepted. Refines [0006](0006-model-judgment-above-a-keyword-floor.md); does not change its floors.

## Context

With automatic routing, the `UserPromptSubmit` hook records the keyword floor of every prompt as "the user's latest message". A route Claude makes later is never lower than that floor, and only workflows named in that message may be lighter than Susanoo's recommendation (ADR 0006).

The Claude Code app also sends prompts the user never typed, and the hook sees them in the same way:

- status reminders (`<system-reminder>`, for example "Linked sessions now: ..." with every linked session's title);
- messages from other sessions (`<cross-session-message>`) and subagents or teammates (`<agent-message>`), which the CLI frames with fixed lines ("Another Claude session sent a message:" ...);
- side-session notes (`<side-sessions-event>`), task notifications (`<task-notification>`), and CI events (`<ci-monitor-event>`).

On 2026-10-15 a peer session's note about a pytest override arrived in the desktop app with two status reminders above it. The session titles in the reminders read as payments and authentication, so the note's floor was DEEP with critical risk, and `susanoo route` for a one-line config change returned DEEP with three safety controls (record su-20261015-003851-9bb5). The peer's own text was FAST. A wrong DEEP route costs a plan, a reviewer pass, and evidence for a trivial change. A peer's words could also name a lighter workflow or ask for autonomy, which only the user may do.

## Options

1. Keep recording every prompt. Simple, but app prompts keep raising routes and can speak for the user.
2. Strip the app's blocks from every prompt before computing the floor. Fixes the bug, but text the user sent along with a block (or pasted) would count less than before: a heuristic would lower a floor.
3. A prompt made **only** of the app's blocks is not the user's message: the recorded user message and any open route stay as they were. A prompt with any other text is the user's message and counts whole, as before. In it, only the requests that lower process (autonomy, a named workflow) ignore the blocks.

## Decision

Option 3.

- **What counts as the app's message.** The prompt consists of complete blocks whose tags are listed in `[auto] app_envelopes` (an opening tag up to its first closing tag), with whitespace between them, plus, only when at least one block is present, lines that start with one of `[auto] app_envelope_frames`. Tags match exactly and case-sensitively. An unclosed block, a look-alike tag (`<system-reminders>`), or a prompt over 100,000 characters is the user's message. Every rule errs toward counting a prompt as the user's.
- **What happens to it.** No route is opened, continued, closed, or replaced. The record of the user's message keeps its floor, autonomy request, and named workflows. Only the turn moves on (a new id and time, kind "ambiguous"), so the route-before-edit gate still stops the first edit Claude makes for it while no route is open, as before. `/susanoo` does not carry over, because the app did not invoke it. Claude gets a short note: the message came from the app or another session, the open route and the user's last message still set the minimum, and `susanoo route` is the way in if it needs code changes.
- **A prompt with user text.** Counted whole for the floor and classification, blocks included, exactly as before: nothing the user sent can lower a route. Asking for autonomy and naming a workflow only count in the user's own text, so a peer's "use the fast workflow" or "work autonomously" quoted in the same prompt does not count.
- **A user who pastes such a block.** The hook cannot tell a paste from the app's own prompt: the event carries only the text. The desktop app wraps pastes in `<pasted_content>`, which is not one of the tags, so a paste there is the user's message. A block pasted on its own, with nothing typed around it, is taken for the app's message. To make it count as yours, type anything with it ("do this:"). This cannot lower anything: the open route and its controls stay, Claude's own route is assessed from its description and kept at or above the last floor, and Claude can raise it.
- **Data, not code.** The tags and frame lines are in `defaults.toml`; a project or user config can change them (an empty `app_envelopes` restores the old behavior). They are validated as tag names and non-empty strings.

## Consequences

- Peer notes, notifications, and status reminders no longer raise or lower a session's route, and cannot grant autonomy or name a lighter workflow.
- The floor stays with the user's last message across app prompts. A later task that only a peer asked for is still routed by Claude, never below that floor or an open route.
- The tag list follows the app's current formats, read from real transcripts. A new kind of app prompt counts as the user's message until it is added, which costs process, not safety. Not covered yet: the CLI's plain-text "[Cross-session idle notice]" (no block) and conversation summaries after compaction.
- A user's words from before the app prompts (a named workflow, an autonomy request) still apply afterwards, as they did for Claude's continuing work in that turn.
