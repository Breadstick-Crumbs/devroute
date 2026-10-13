"""Render Susanoo's brand assets into docs/assets/.

- logo.svg: the mark, an original faceted kabuto (armored helmet) with a lightning crest.
- banner-light.png / banner-dark.png: README banners (rendered to PNG so every viewer sees the
  same fonts).
- social-preview.png: the 1280x640 repository link preview (upload it in the repository's
  Settings > Social preview). The terminal lines come from real `susanoo route` results.

Rendering the PNGs needs a Chromium-based browser (Edge or Chrome) and the Cascadia Code and
Yu Gothic fonts. Run from the repository root:

    python scripts/make_brand_assets.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"

MONO = "'Cascadia Code','Cascadia Mono','SF Mono',Consolas,monospace"
JP = "'Yu Gothic UI','Yu Gothic','Hiragino Sans','Meiryo',sans-serif"
BG, PANEL, BAR, EDGE = "#0b0a12", "#13111d", "#1a1727", "#2a2640"
INK, MUTED, DIM = "#ece9f7", "#a7a3c2", "#5b5675"
VIOLET, AMBER = "#a78bfa", "#fbbf24"
MODE_COLORS = {"fast": "#4ade80", "standard": "#60a5fa", "deep": "#c4b5fd"}
TAGLINE = ("Route every coding task to the", "right workflow in Claude Code.")
CARD_TASKS = [
    "Fix typo in README",
    "Add pagination to the /users API endpoint",
    "Build a SaaS dashboard with auth and billing",
]

# The mark, in a 64x64 box: horns and a lightning crest over a faceted helmet bowl, brim, side
# guards, a two-tier neck guard, and a dark face opening with amber eye slits.
MARK = f"""<path d="M27 21 13 1l9 6 7 11z" fill="#c4b5fd"/>
<path d="M37 21 51 1l-9 6-7 11z" fill="#a78bfa"/>
<path d="M35.5 0 27 13.5h5.2L29 24l9.5-13.5h-5.3z" fill="{AMBER}"/>
<path d="M9 45 4 53l28 8 28-8-5-8-23 6z" fill="#3b0f86"/>
<path d="M11 37 5 46l27 7 27-7-6-9-21 6z" fill="#5b21b6"/>
<path d="M13 32 1 25l2 15 10-1z" fill="#8b5cf6"/>
<path d="M51 32 63 25l-2 15-10-1z" fill="#6d28d9"/>
<path d="M32 15H24l-8 6-4 11h20z" fill="#8b5cf6"/>
<path d="M32 15h8l8 6 4 11H32z" fill="#6d28d9"/>
<path d="M24 15l-3 17M40 15l3 17M32 15v17" stroke="#2e1065" stroke-opacity="0.35" stroke-width="1"/>
<path d="M7 31h50l-5 5.5H12z" fill="#ede9fe"/>
<path d="M18 36.5h28L42 48l-10 5-10-5z" fill="{BG}"/>
<path d="M21.5 40.5l7.5 1.7M42.5 40.5 35 42.2" stroke="{AMBER}" stroke-width="2.2" stroke-linecap="square"/>"""


def mark(x: float, y: float, scale: float) -> str:
    return f'<g transform="translate({x} {y}) scale({scale})">{MARK}</g>'


def text(
    x: float, y: float, content: str, size: float, fill: str, family: str = MONO, attrs: str = ""
) -> str:
    return f'<text x="{x}" y="{y}" fill="{fill}" font-family="{family}" font-size="{size}" {attrs}>{content}</text>'


def logo() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 64 64" role="img" '
        'aria-labelledby="t">\n<title id="t">Susanoo logo: an armored helmet with a lightning crest</title>\n'
        f"{MARK}\n</svg>\n"
    )


def banner(dark: bool) -> str:
    ink, muted, accent = (INK, MUTED, VIOLET) if dark else ("#1f1b2e", "#57536b", "#6d28d9")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="260" viewBox="0 0 1200 260">
{mark(40, 34, 3.1)}
{text(268, 78, "スサノオ", 22, accent, JP, 'font-weight="500" letter-spacing="6"')}
{text(262, 158, "susanoo", 96, ink, MONO, 'font-weight="700" letter-spacing="-3"')}
{text(268, 208, " ".join(TAGLINE), 23, muted)}
</svg>"""


def route(task: str) -> dict[str, Any]:
    out = subprocess.run(
        [sys.executable, "-m", "susanoo", "route", "--no-record", "--json", task],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data: dict[str, Any] = json.loads(out)
    return data


def social() -> str:
    rows = []
    y = 222
    for task in CARD_TASKS:
        data = route(task)
        mode = data["profile"]["name"]
        controls = "  ".join(f"✓ {c['id']}" for c in data["route"]["controls"][:2])
        command = (
            f'<tspan fill="{MODE_COLORS["fast"]}">$</tspan> susanoo route '
            f'<tspan fill="#c7b9ff">"{escape(task)}"</tspan>'
        )
        result = (
            f'  <tspan fill="{DIM}">→ mode</tspan> '
            f'<tspan fill="{MODE_COLORS[mode]}" font-weight="700">{mode.upper()}</tspan>'
            + (f'<tspan fill="{AMBER}">  {controls}</tspan>' if controls else "")
        )
        rows += [text(664, y, command, 15, INK), text(664, y + 26, result, 15, INK)]
        y += 78
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">
<defs>
  <pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">
    <circle cx="1.5" cy="1.5" r="1.2" fill="#ffffff" fill-opacity="0.05"/>
  </pattern>
  <radialGradient id="glow" cx="0.22" cy="0.3" r="0.6">
    <stop offset="0" stop-color="#4c1d95" stop-opacity="0.55"/><stop offset="1" stop-color="{BG}" stop-opacity="0"/>
  </radialGradient>
</defs>
<rect width="1280" height="640" fill="{BG}"/>
<rect width="1280" height="640" fill="url(#glow)"/>
<rect width="1280" height="640" fill="url(#dots)"/>
{mark(72, 52, 2.3)}
{text(76, 268, "スサノオ", 22, VIOLET, JP, 'font-weight="500" letter-spacing="6"')}
{text(70, 352, "susanoo", 108, INK, MONO, 'font-weight="700" letter-spacing="-4"')}
{text(76, 408, TAGLINE[0], 25, MUTED)}
{text(76, 442, TAGLINE[1], 25, MUTED)}
{text(76, 556, "plan approval · verified evidence · safety gates", 18, DIM)}
<rect x="640" y="150" width="584" height="300" rx="12" fill="{PANEL}" stroke="{EDGE}"/>
<path d="M640 162a12 12 0 0 1 12-12h560a12 12 0 0 1 12 12v22H640z" fill="{BAR}"/>
<circle cx="662" cy="167" r="5.5" fill="#3b3655"/>
<circle cx="682" cy="167" r="5.5" fill="#3b3655"/>
<circle cx="702" cy="167" r="5.5" fill="#3b3655"/>
{chr(10).join(rows)}
{text(640, 556, "github.com/Breadstick-Crumbs/susanoo", 18, DIM)}
</svg>"""


def browser() -> str:
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    raise SystemExit("a Chromium-based browser (Edge or Chrome) is needed to render PNGs")


def render(svg: str, out: Path, width: int, height: int, scale: int, transparent: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "page.svg"
        source.write_text(svg, encoding="utf-8")
        args = [
            browser(),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--window-size={width},{height}",
            f"--screenshot={out}",
        ]
        if transparent:
            args.append("--default-background-color=00000000")
        subprocess.run([*args, source.as_uri()], check=True, capture_output=True)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "logo.svg").write_text(logo(), encoding="utf-8", newline="\n")
    render(banner(dark=False), ASSETS / "banner-light.png", 1200, 260, 2, transparent=True)
    render(banner(dark=True), ASSETS / "banner-dark.png", 1200, 260, 2, transparent=True)
    render(social(), ASSETS / "social-preview.png", 1280, 640, 1, transparent=False)
    print("wrote logo.svg, banner-light.png, banner-dark.png, social-preview.png in docs/assets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
