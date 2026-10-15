"""Render docs/assets/demo.svg: an animated terminal showing real `susanoo route` results.

The content comes from `susanoo route --json` for each example task, so the demo stays true to
the current heuristics. Rerun after changing routing:

    python scripts/make_demo_svg.py

Only environment-independent parts are shown (signals, assessment levels, mode, safety controls,
approval steps), so the output does not depend on which tools are installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "demo.svg"
TASKS = [
    "Fix typo in README",
    "Add pagination to the /users API endpoint",
    "Build a SaaS dashboard with authentication and billing",
]

WIDTH, LINE, FONT, CHAR = 900, 22, 14, 8.43  # CHAR: advance of a 14px monospace glyph
LEFT, TOP = 24, 70
TYPE_SPEED, PAUSE, STAGGER, HOLD = 0.035, 0.45, 0.12, 3.6  # seconds
COLORS = {  # the brand palette (scripts/make_brand_assets.py)
    "bg": "#13111d",
    "bar": "#1a1727",
    "border": "#2a2640",
    "dot": "#3b3655",
    "text": "#ece9f7",
    "muted": "#7d789a",
    "prompt": "#4ade80",
    "string": "#c7b9ff",
    "low": "#4ade80",
    "moderate": "#fbbf24",
    "high": "#fb923c",
    "critical": "#f87171",
    "control": "#fbbf24",
    "fast": "#4ade80",
    "standard": "#60a5fa",
    "deep": "#c4b5fd",
}

Span = tuple[str, str]  # (text, color key)


def route(task: str) -> dict[str, Any]:
    # Without a session id, so the latest message of a Claude Code session running this script
    # is not combined into the demo's routes.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    out = subprocess.run(
        [sys.executable, "-m", "susanoo", "route", "--no-record", "--json", task],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data: dict[str, Any] = json.loads(out)
    return data


def scene(task: str) -> list[list[Span]]:
    """Output lines (after the command) for one task, as colored spans."""
    data = route(task)
    a, profile, controls = data["assessment"], data["profile"], data["route"]["controls"]
    signals = [s["label"] for s in a["signals"] if s["id"] != "trivial-edit"]
    lines: list[list[Span]] = [[("  signals      ", "muted"), (" · ".join(signals) or "none", "text")]]
    levels: list[Span] = [("  assessment   ", "muted")]
    for name in ("complexity", "risk", "uncertainty"):
        level = a[name]["level"]
        levels += [(f"{name} ", "muted"), (level.upper(), level), ("   ", "muted")]
    lines.append(levels)
    lines.append([])
    mode = profile["name"]
    lines.append(
        [
            ("  ▶ mode       ", "muted"),
            (mode.upper(), mode),
            (f"   {profile['summary'].split('.')[0]}.", "text"),
        ]
    )
    if controls:
        lines.append(
            [("  controls     ", "muted"), ("   ".join(f"✓ {c['id']}" for c in controls), "control")]
        )
    approvals = [p["title"].lower() for p in profile["phases"] if p.get("approval")]
    if approvals:
        lines.append([("  approval at  ", "muted"), (" → ".join(approvals), "text")])
    return lines


def tspans(spans: list[Span]) -> str:
    return "".join(f'<tspan fill="{COLORS[c]}">{escape(t)}</tspan>' for t, c in spans)


def visibility(name: str, on: float, off: float, total: float) -> str:
    """Keyframes showing an element from ``on`` to ``off`` seconds in a ``total`` cycle."""
    a, b = on / total * 100, min(off / total * 100, 99.9)
    return (
        f"@keyframes {name}{{0%,{a - 0.01:.3f}%{{opacity:0}}{a:.3f}%,{b:.3f}%{{opacity:1}}"
        f"{b + 0.01:.3f}%,100%{{opacity:0}}}}"
    )


def build() -> str:
    scenes = [(task, scene(task)) for task in TASKS]
    rows = max(len(lines) for _, lines in scenes) + 1
    height = TOP + rows * LINE + 24
    styles: list[str] = []
    body: list[str] = []
    t = 0.6
    timeline = []
    for task, lines in scenes:
        start = t
        typed = start + len(task) * TYPE_SPEED
        appear = [typed + PAUSE + i * STAGGER for i in range(len(lines))]
        end = (appear[-1] if appear else typed) + HOLD
        timeline.append((task, lines, start, typed, appear, end))
        t = end + 0.4
    total = t
    for n, (task, lines, start, typed, appear, end) in enumerate(timeline):
        last = n == len(timeline) - 1
        styles.append(visibility(f"s{n}", start, end, total))
        command: list[Span] = [("$ ", "prompt"), ("susanoo route ", "text"), (f'"{task}"', "string")]
        width = sum(len(text) for text, _ in command) * CHAR + 4
        typed_from = LEFT + len("$ ") * CHAR
        a, b = start / total * 100, typed / total * 100
        chars = len(task) + len("susanoo route ") + 2
        styles.append(
            f"@keyframes t{n}{{0%,{a:.3f}%{{transform:translateX(0);animation-timing-function:steps({chars},end)}}"
            f"{b:.3f}%,100%{{transform:translateX({width:.1f}px)}}}}"
        )
        group = [f'<g class="scene{" final" if last else ""}" style="animation:s{n} {total:.2f}s infinite">']
        group.append(f'<text x="{LEFT}" y="{TOP}">{tspans(command)}</text>')
        group.append(
            f'<rect class="cover" x="{typed_from:.1f}" y="{TOP - 16}" width="{width:.1f}" height="22" '
            f'fill="{COLORS["bg"]}" style="animation:t{n} {total:.2f}s infinite"/>'
        )
        for i, spans in enumerate(lines):
            if not spans:
                continue
            styles.append(visibility(f"l{n}_{i}", appear[i], end, total))
            y = TOP + (i + 1) * LINE + 6
            group.append(
                f'<text x="{LEFT}" y="{y}" style="animation:l{n}_{i} {total:.2f}s infinite">{tspans(spans)}</text>'
            )
        group.append("</g>")
        body += group
    reduced = (
        "@media (prefers-reduced-motion: reduce){.scene,.scene text,.cover{animation:none!important}"
        ".scene{opacity:0}.scene.final,.scene.final text{opacity:1}.cover{display:none}}"
    )
    title = (
        "Susanoo routes three tasks: a typo fix to FAST, an API change to STANDARD, and a SaaS build to DEEP"
    )
    desc = " ".join(f"{task}: {lines[3][1][0] if len(lines) > 3 else ''}" for task, lines in scenes)
    mono = '"Cascadia Code","Cascadia Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace'
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}"
 viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title>
<desc id="desc">{escape(desc)} (condensed output of susanoo route; generated by scripts/make_demo_svg.py)</desc>
<style>
text{{font-family:{mono};font-size:{FONT}px;white-space:pre}}
.scene text{{opacity:0}}.scene text:first-of-type{{opacity:1}}
{"".join(styles)}
{reduced}
</style>
<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10" fill="{COLORS["bg"]}" stroke="{COLORS["border"]}"/>
<path d="M0.5 36V10.5a10 10 0 0 1 10-10h{WIDTH - 21}a10 10 0 0 1 10 10V36z" fill="{COLORS["bar"]}"/>
<circle cx="22" cy="18" r="5.5" fill="{COLORS["dot"]}"/>
<circle cx="42" cy="18" r="5.5" fill="{COLORS["dot"]}"/>
<circle cx="62" cy="18" r="5.5" fill="{COLORS["dot"]}"/>
<text x="{WIDTH / 2}" y="23" text-anchor="middle" fill="{COLORS["muted"]}" style="font-size:12px">susanoo route</text>
{chr(10).join(body)}
</svg>
"""


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
