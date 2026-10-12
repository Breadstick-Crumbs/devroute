"""Regenerate the example briefs in this directory.

Usage (from the repository root, with DevRoute installed):

    python examples/generate.py

Each example runs `devroute route` against examples/sample-app and writes the
brief to a Markdown file. Tool availability depends on the machine, so your
"Capabilities" sections may differ from the committed copies; the assessment
and routing sections are deterministic.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from devroute.cli import main

HERE = Path(__file__).resolve().parent
APP = HERE / "sample-app"

EXAMPLES = [
    ("01-simple.md", "Simple change", "Fix the typo in the OutOfStockError message"),
    (
        "02-moderate.md",
        "Moderate feature",
        "Add a low-stock alert that emails the warehouse when a SKU drops below its reorder level",
    ),
    (
        "03-complex.md",
        "Complex, high-risk build",
        "Build a multi-warehouse inventory platform with user authentication, Stripe billing, "
        "and an admin dashboard",
    ),
]


def run(*args: str) -> str:
    out = io.StringIO()
    code = main([*args, "--path", str(APP)], stdout=out, stderr=io.StringIO())
    if code != 0:
        raise SystemExit(f"devroute {' '.join(args)} exited with {code}")
    return out.getvalue()


def generate() -> None:
    for filename, title, task in EXAMPLES:
        brief = run("route", "--no-record", task)
        body = (
            f"# Example: {title}\n\n"
            f"Command (run against `examples/sample-app`):\n\n"
            f'```bash\ndevroute route --path examples/sample-app "{task}"\n```\n\n'
            "Output:\n\n"
            "````markdown\n" + brief.rstrip() + "\n````\n"
        )
        (HERE / filename).write_text(body, encoding="utf-8", newline="\n")
        print(f"wrote {filename}", file=sys.stderr)


if __name__ == "__main__":
    generate()
