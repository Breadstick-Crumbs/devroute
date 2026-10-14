# 0008: UI and UX work

**Status:** accepted, partly implemented (v0.8, unreleased): the UI reference suite, the suite's `controls` field, the UI checks (visual check, accessibility scan, visual regression, website performance budget), UI domains with their checklists, and design context. The routing changes are not built yet; [roadmap](../roadmap.md) tracks each part. Works within [0005](0005-safety-controls-independent-of-profile.md), [0006](0006-model-judgment-above-a-keyword-floor.md), and [0007](0007-hands-off-by-default.md).

## Context

In v0.7, Susanoo knows little about user interfaces:

- **One signal.** The `ui` signal matches words such as page, component, css, layout, and button. It raises affected components and testing difficulty by one level each, which alone can lift a small UI change to STANDARD, and it *recommends* browser tests in STANDARD and DEEP. Nothing is ever *required* for UI work: no look at the rendered result, no accessibility check, no performance check.
- **No UI guidance.** The brief says nothing about responsive behavior, the empty, loading, and error states, accessibility basics, or what matters for the kind of product being built.
- **Heavy routes for visual work.** "Redesign the pricing page layout for mobile" routes DEEP, because "redesign" counts as an architectural change. "Change the checkout button color to green" routes DEEP with the payment controls, because "checkout" counts as payments.
- **Friction while iterating.** A burst of small "tighter spacing" messages each pays the hook start-up, and route-before-edit denies the first edit of every unrouted message.

The one check that catches most UI defects is looking at the result: render it and compare it with what was asked for. Reading the code does not show overlapping elements, a broken mobile layout, or low contrast. Claude can do this when a browser tool is available (a browser MCP server, Claude in Chrome, the desktop app's browser, or Playwright), but nothing asks it to, and nothing records that it happened. Discovery can only see some of these: browser MCP servers in MCP configuration files (by name) and Playwright in the project. Browsers built into Claude Code or the desktop app are not in those files.

## Options

1. **Leave UI to Claude's general judgment.** No change. Visual checks keep happening only when Claude happens to do them.
2. **Judge UI quality in Susanoo.** Score layouts, contrast, or screenshots. That needs rendering, image analysis, or a model call: runtime dependencies and nondeterminism that ADR 0001 and ADR 0002 rule out. Aesthetic judgment is also not something a keyword router can do.
3. **Size, require, and record UI checks; give short, sourced checklists; leave the judging to Claude, the project's tools, and people.** Same approach as for tests and security: Susanoo decides *which* checks a task needs and demands evidence that they happened.

## Decision

Option 3. Susanoo sizes UI tasks, requires and records UI checks, and gives domain checklists. It never judges aesthetics and never claims a UI is good.

### What Susanoo will do

- **Require a visual check** (`visual_check`) for UI tasks in STANDARD and DEEP, and recommend it in FAST. Claude renders the change, looks at it at the relevant widths (by default 375, 768, and 1280 CSS px: common conventions, not a standard), and compares it with the request or a reference. The evidence is *reported*, never *executed*, because Susanoo does not run it: `susanoo evidence latest --step visual_check --status passed --note "375/768/1280 checked; screenshots in ..."`. To keep it that way, `visual_check` gets its own capability, and its rule says `evidence = "reported"`, so the step is `manual` or `unavailable` and never `ready`, even if a project tool entry gives the capability a command; if it reused `browser_testing`, an installed Playwright would make it `ready`, and the gate would demand a passing `playwright test` run instead of a look at the result. A visual check that cannot apply (a CSV export from a page that has no visual change) is recorded as `skipped` with that reason, which the evidence gate accepts for manual steps.
- **Accessibility scans** (`accessibility_scan`) are *recommended* in STANDARD and *required* in DEEP for UI tasks. They run the project's own runner as an argv command, so a passing run is executed evidence: the Playwright suite when `@axe-core/playwright` is installed (it passes whenever the suite passes, so it means something only if tests call AxeBuilder), or `pa11y-ci` when its config exists. The `axe` CLI needs a URL, so with it the scan is manual. In DEEP, a project with an installed runner therefore cannot close a UI task until a scan passes, the same as integration tests in DEEP today; a scan that needs a running server is part of that setup. Without a runner the step is a gap and follows the rule below. Automated scans catch only part of the problems: Deque, the maker of axe-core, reports that axe found about 57% of issues by volume in its own audit data. The brief says so and keeps keyboard and focus checks in the checklist.
- **Which tools count for the visual check:** a browser MCP server (by name), Playwright in the project (Claude takes the screenshots, for example with `npx playwright screenshot`), and the Claude desktop app's browser pane, detected from the `CLAUDE_CODE_DESKTOP_APP_VERSION` variable the app sets. An installed skill or subagent whose name or description matches the capability's keywords also counts, as for every capability (a `susanoo-capabilities` frontmatter entry overrides the inference). Claude in Chrome and other browsers Susanoo cannot see can be declared with a project `[[tools]]` entry that lists `visual_check` (see [configuration](../configuration.md#register-an-in-house-tool)). Tests cover both the packaged registry and a project entry with a command.
- **Performance budgets** (`performance_budget`, Lighthouse CI) are *recommended* for the `website` domain only, never required. The rule applies only to the `website` domain (`ui_domains = ["website"]`).
- **Visual regression** (Playwright `toHaveScreenshot`) is *recommended* only when the project already has baseline screenshots (`*-snapshots` folders at the top level or one level down in `tests/`, `test/`, `e2e/`, or `tests/e2e/`; deeper folders and a custom `snapshotPathTemplate` are not found). Otherwise the step is left out entirely rather than shown as a gap (`only_with_tool`), unless something makes it required, such as a safety control. Susanoo never creates baselines.
- **Browser tests** keep their current rule.
- **Pick a UI domain** (`website`, `ecommerce`, `dashboard`, `mobile`, `component-library`) for a 5-8 item checklist and the checks it emphasizes, in this order:
  1. `[ui] domain` in the project or user configuration;
  2. words in the task: first phrases that name the product in any domain ("landing page", "admin dashboard", "checkout", "React Native"), then generic words ("chart", "cart"); "for mobile" alone means a responsive page, not a mobile app;
  3. project detection, read-only: dependencies such as `@tanstack/react-table` or `recharts` (dashboard), `react-native` or `expo` (mobile), Shopify storefront packages such as `@shopify/hydrogen` (ecommerce), Storybook without an app framework such as Next.js or React Router, and only when nothing else is detected (component library);
  4. none: a generic UI checklist only.

  Domains are tried from the most specific (mobile, component library, ecommerce, dashboard) to the most general (website), and `[ui] domain = "none"` keeps only the baseline. Domains, their words, detection hints, and checklists are data in `ui_domains.toml`. Task words ignore negation ("don't touch the dashboard" still picks dashboard), which only changes a checklist. The brief always says which domain was chosen and why ("UI domain: dashboard (detected from @tanstack/react-table; set [ui] domain to change)"). It is a heuristic and is never described as validated.
- **A universal baseline** in every UI brief: WCAG 2.2 AA essentials (contrast at least 4.5:1 for body text, pointer targets at least 24x24 CSS px, focus never fully hidden behind sticky content, full keyboard operation with a visible focus indicator), the empty, loading, and error states plus long content, and the breakpoints above. The compact auto-mode brief gets one line, not the full list.
- **Point Claude at design context**, read-only: `DESIGN.md` or `docs/design*.md`, token files (`tailwind.config.*`, `*.tokens.json`, token folders, and global stylesheets that define a theme: a Tailwind v4 `@theme` block or at least five custom properties, read up to 64 KB), Storybook (`.storybook/` or a Storybook dependency), and a Figma MCP server or plugin (by name only, as for every MCP server; connectors added in claude.ai are not in Claude Code's configuration files and are not seen). The brief lists paths, never contents, and tells Claude to read and follow them. When a project has UI work but none of these, the brief suggests creating a `DESIGN.md`. Susanoo never creates it. The patterns and wording are data (`[design_context]` in `ui_domains.toml`).
- **Personal taste is a separate skill.** A `taste-ui` skill, installed by `susanoo init` and maintained separately, holds the user's own taste profile. When it is installed, UI briefs say: follow it as a quality bar, not a required look; the project's design context and client brand guidelines win where they conflict; ask the user when the fit is unclear.

### What Susanoo will not do

- Judge layout, color, typography, or copy; produce or compare screenshots itself; or claim a UI meets WCAG. A passing scan means "the scanner found nothing", and the brief says so.
- Add questions or confirmations for the user. Every new check goes through the existing evidence gate (ADR 0007).
- Add runtime dependencies. Playwright, axe, pa11y, and Lighthouse are the project's tools, run as commands.
- Cite specific rules from paywalled research (Baymard's checkout studies) or present practitioner guidance (dashboard and data-table advice) as research. Checklists name their sources and say which kind each is.

### Required and recommended checks

For tasks where the `ui` signal matches (after the routing changes below):

| Check | FAST | STANDARD | DEEP | Evidence |
|---|---|---|---|---|
| `visual_check` | recommended | **required** | **required** | reported, with widths and screenshot paths |
| `accessibility_scan` | — | recommended | **required** | executed when a runner is installed |
| `browser_tests` | — | recommended | recommended | executed (unchanged) |
| `visual_regression` | — | recommended if baselines exist | recommended if baselines exist | executed |
| `performance_budget` | — | recommended, `website` only | recommended, `website` only | executed |

When a required check has no usable tool, the existing rules apply: with `/susanoo`, the step needs reported evidence (a `skipped` with the reason counts); on automatically routed tasks, a check whose tool is missing is reported, not demanded. A required check whose tool *can* run (`ready`) accepts only a passing run that Susanoo executed; a reported result or a skip does not count.

**Not adopted: accessibility scans required in STANDARD whenever a runner is installed.** A required runnable step needs a passing run that Susanoo executed, and scanners such as `pa11y-ci` need a running server and configured URLs. Requiring them would block STANDARD tasks on environment problems rather than accessibility problems, and would need a new kind of rule (escalation on tool availability). DEEP accepts that cost, as it already does for integration tests; STANDARD should not. This can be revisited once outcome records show how often recommended scans are skipped.

### Routing changes

- **Visual redesign is not an architectural change.** "Redesign" whose object is a page, layout, screen, or component should not raise architectural complexity. "Redesign the database schema behind the orders page" and "redesign the frontend state management architecture" still should.
- **Presentation on payment pages is not payment logic.** A task whose payment words name only a page or element ("checkout page", "checkout button", "billing page") and whose other words are presentational (layout, styling, color, spacing, copy, responsive) should not add the payment controls. Any word for payment logic keeps them: calculating, charging, totals, prices or discounts that must match billing, tax, refunds, subscriptions, invoices, Stripe or another provider, webhooks, or rebuilding the checkout *flow* or its form submission. ("Pricing page" is not a payment word today, only "pricing plan" is; the pricing page redesign routes DEEP because of "redesign".) A missed payment control is a safety bug and an extra one is only extra process, so this errs strict: when in doubt, the controls stay.
- **Evaluation gates.** The UI cases live in a separate suite, [`ui-v1.toml`](../../src/susanoo/data/suites/ui-v1.toml), labeled with their rationale before any heuristic was changed. They are kept out of `routing-v1` so its cases and CI's 90% agreement threshold on it are untouched while the fixes land. Suite cases can now list `controls` the route must include; a missing one counts as a risk-floor violation. Every routing change must keep 0 risk-floor violations on both suites and leave every `routing-v1` outcome unchanged unless a labeled reason is recorded.

  Baseline before any change (v0.7 heuristics): `ui-v1` agreement 8 of 15, 0 risk-floor violations. Mismatches: the pricing page and onboarding-screen redesigns, the responsive checkout form, and the checkout button color route DEEP; the navbar spacing and button-variant tasks route STANDARD (the button variant because the `ui` signal's own dimension effects lift it; the navbar task because no signal matches, so confidence is low); the landing page routes FAST.

### Iteration friction

Measured first, changed later. Before changing route-before-edit or hook behavior for bursts of small UI tweaks, measure how often they trigger, then propose a change (for example, follow-up tweaks continue the open UI route) and get the user's agreement. No gate behavior changes as part of this decision.

## Consequences

- When a browser tool is detected, or the task was routed with `/susanoo`, STANDARD and DEEP UI tasks cannot be closed without a recorded visual check or an explained skip. Automatically routed tasks in a project with no detected browser tool are not held to it (see below). That is more work per UI task, and it is the point: the check that catches most UI defects becomes routine and visible in records.
- The guarantee depends on Claude's honesty about a reported check, as for every manual step. Susanoo records what was claimed; it cannot see the screenshots.
- The `ui` signal is broad ("page" matches "the reports page"), so some tasks that change no pixels will ask for a visual check. Recording `skipped` with the reason is cheap; narrowing the signal is an eval-gated change.
- Without a browser tool the visual check stays a gap: `/susanoo` tasks need an explained skip, automatically routed tasks report it. The brief names the gap so the user can add a tool.
- Domain detection is keyword- and dependency-based and will sometimes be wrong; `[ui] domain` fixes it per project.
- Checklists cite standards (WCAG 2.2, Core Web Vitals thresholds, platform guidelines) as standards and practitioner advice as advice. Susanoo makes no claim that following them produces a good UI.
- `susanoo eval` measures routing only. Whether these checks improve UI outcomes needs recorded outcomes, as for every other check.
