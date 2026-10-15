# Roadmap

This roadmap separates what is **implemented**, what is **planned**, and what is **research**. Research means the approach itself is unproven and may be dropped. No delivery dates are promised. Phases are ordered by dependency, not by calendar. Each planned item is tracked as a GitHub issue under the matching milestone.

Legend: ✅ implemented in v0.1 · 🔜 planned · 🔬 research

## Phase 1: Core framework (v0.1)

| Item | Status |
|---|---|
| Ten-dimension, explainable task assessment with independent complexity, risk, and uncertainty | ✅ |
| FAST / STANDARD / DEEP routing with reasons, overrides, mandatory controls, critical-risk floor | ✅ |
| CLI: `doctor`, `inventory`, `assess`, `route`, `init`, `report`, plus `verify`, `evidence`, `outcome`, `research`, `eval` | ✅ |
| `/susanoo` Claude Code skill and `susanoo-reviewer` / `susanoo-researcher` subagents with safe install and uninstall | ✅ |
| Test suite, CI (lint, types, tests on 3 OSes, security checks) | ✅ |

**Acceptance criteria (met):** the package installs with no runtime dependencies; every CLI command has automated tests; the routing suite runs deterministically with 0 risk-floor violations; `susanoo init` never modifies a file it did not create.

## Phase 2: Capability intelligence

| Item | Status |
|---|---|
| Read-only discovery of languages, manifests, tools, skills, subagents, commands, MCP servers, plugins | ✅ |
| Five-state capability status, opt-in version verification | ✅ |
| Registry-based tool selection that prefers CLI and native tools over MCP servers | ✅ |
| Briefs name installed skills, subagents, and MCP servers per capability; task keywords and the project's JavaScript package manager shape tool choice and commands | ✅ (v0.7) |
| Discovery of plugin-provided skills and agents (inside installed plugin directories) | 🔜 |
| Monorepo awareness: per-package inventories and task-to-package scoping | 🔜 |
| Integration validation: safe smoke checks per tool beyond `--version` (e.g. `pytest --collect-only`) | 🔜 |
| Optional `ResearchProvider`s for package indexes and MCP registries, with provenance and license data | 🔜 |

**Acceptance criteria:** discovery covers plugin assets and workspaces with tests for each; validation results distinguish "runs" from "runs correctly on this project"; external research providers are opt-in, cite sources, and never install anything.

## Phase 3: Adaptive execution

| Item | Status |
|---|---|
| Task decomposition: split DEEP tasks into independently verifiable work units with explicit interfaces | 🔜 (design proposed: [ADR 0012](decisions/0012-delegation-plans.md)) |
| Delegation plans: which units suit parallel subagents or worktrees, and which share files and must stay serial | 🔜 (design proposed: [ADR 0012](decisions/0012-delegation-plans.md)) |
| Landing ([ADR 0014](decisions/0014-susanoo-land.md)): `susanoo land` tests several agent branches on one combined tree, keeps both sides of append-only conflicts, attributes failures (broken alone, interaction, base), pushes base merges, and merges pull requests with `gh` only with the user's `--merge`; runs are recorded for the go or no-go | ✅ (unreleased) |
| Landing: leases and interface-change notices ([ADR 0013](decisions/0013-leases-and-landing-queue.md)), built only if the land records show they are needed | 🔬 |
| Strategy selection within profiles (test-first, spike-then-build, migration-first) based on signals | 🔜 |
| Stronger verification: coverage-delta checks, changed-files-to-tests mapping, migration dry runs | 🔜 |
| Optional hooks that block completion claims when required evidence is missing | ✅ (v0.2, `--enforce`) |
| Settings-scoped enforcement option, so gates also apply in resumed sessions and compaction recovery works without a manual snippet ([#29](https://github.com/Breadstick-Crumbs/susanoo/issues/29)) | ✅ (v0.4, `--enforce-scope settings`) |
| Global auto mode: the default workflow in every project, with automatic routing and autonomy-aware gates ([#34](https://github.com/Breadstick-Crumbs/susanoo/issues/34)) | ✅ (v0.6, `susanoo init --global`) |
| UI work ([ADR 0008](decisions/0008-ui-ux-workflow.md)): UI reference suite with labeled cases and `controls` checks | ✅ (unreleased) |
| UI work: required visual check and accessibility scans, with project tools (axe, pa11y, Playwright screenshots; Lighthouse CI detected) | ✅ (unreleased) |
| UI work ([ADR 0010](decisions/0010-taste-skill-and-screenshot-helper.md)): `taste-ui` skill installed by `susanoo init`, personal taste profile, `susanoo taste shoot / capture` | ✅ (unreleased) |
| UI work: UI domain (website, ecommerce, dashboard, mobile, component library) with short checklists in the brief, and the website performance budget | ✅ (unreleased) |
| UI work: read-only discovery of design context (`DESIGN.md`, tokens, Storybook, Figma MCP by name) | ✅ (unreleased) |
| UI work: visual redesigns routed without architectural weight, eval-gated (payment-page styling deliberately keeps its payment controls) | ✅ (unreleased) |
| Architecture context ([ADR 0011](decisions/0011-architecture-context.md)): STANDARD and DEEP briefs name architecture documents, decision records, `AGENTS.md`/`CLAUDE.md` architecture sections, and boundary rules; import-linter, dependency-cruiser, and eslint-plugin-boundaries run as a recommended check | ✅ (unreleased) |
| Architecture context: run ArchUnit rules (needs a Maven or Gradle test runner) | 🔜 |
| UI work: measure, then reduce, gate friction during bursts of small UI tweaks | 🔬 |

**Acceptance criteria:** decomposition output is machine-readable and testable; delegation never assigns overlapping files to parallel units; the verification gate can be enabled per project and is covered by tests.

## Phase 4: Measurement and optimization

| Item | Status |
|---|---|
| Local route, outcome, and evidence records; per-profile summaries; gated recommendations | ✅ |
| Deterministic routing evaluation suite | ✅ |
| Automatic capture of duration, tokens, and cost from Claude Code session data where available (opt-in `[records] capture_usage`; cost only when the transcript records one) | ✅ (unreleased) |
| Workflow comparison harness: paired tasks run under different profiles with blinded review | 🔜 |
| Exportable, anonymized benchmark format for sharing results | 🔜 |

**Acceptance criteria:** published comparisons include sample sizes, variance, and the full protocol, and no benchmark claim is made without them.

## Phase 5: Advanced intelligence (research)

| Track | Status | Notes |
|---|---|---|
| Context optimization: selective retrieval, reusable repository knowledge, context isolation between agents | 🔬 | Measure context use before and after; keep what reduces tokens without hurting outcomes. |
| Prompt caching | 🔬 | Provider prompt caching is a supported API feature. Research whether brief and skill structure can keep cache-friendly prefixes. |
| KV-cache and representation reuse | 🔬 | Claude Code does **not** expose model-internal KV tensors. Any work here is limited to provider-supported caching, or to experiments with open models outside Claude Code. |
| Executable simulation environments | 🔬 | Synthetic users, API fault injection, database state fixtures, concurrency and race testing. Verifiable execution comes before predictive "world models". |
| Learned or model-assisted routing | ✅ partly | Implemented: automatic routing lets Claude route unrouted messages and raise routes, above a keyword floor ([ADR 0006](decisions/0006-model-judgment-above-a-keyword-floor.md)). A learned router is still research, evaluated against the heuristic baseline on recorded outcomes. |
| Dynamic model routing and compute budgets | 🔬 | Depends on reliable cost and outcome data from phase 4. |
| Learning from verified outcomes | 🔬 | Suggestions stay human-reviewed. No silent configuration changes. |

## Phase 6: Ecosystem

| Item | Status |
|---|---|
| Distribute the skill and subagents as a Claude Code plugin and marketplace entry | 🔜 |
| Publish to PyPI | 🔜 |
| Community tool registry contributions with review guidelines | 🔜 |
| Shared workflow profiles and signal packs (e.g. "fintech", "data platform") | 🔜 |
| Adapters for other AI coding environments | 🔜 |

**Acceptance criteria:** plugin install and uninstall are documented and tested; registry contributions require detection rules plus tests; each adapter passes the same CLI-level contract tests.
