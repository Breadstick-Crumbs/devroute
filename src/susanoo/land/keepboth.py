"""Resolve textual conflicts by keeping both sides, only where both sides only added lines.

A conflicted file (``merge.conflictStyle = zdiff3``) is a sequence of plain lines and conflict
hunks::

    <<<<<<< ours
    ...ours...
    ||||||| base
    ...base...
    =======
    ...theirs...
    >>>>>>> theirs

A hunk is resolved only when its base part is empty (both sides inserted lines at the same
place and neither removed or changed any), no added line is a Markdown heading, and the hunk
lies where a ``[[land.keep_both]]`` rule allows it. The base branch's lines always come first
(or the lines are sorted, when the rule says so), so a conflict resolves to the same bytes
whichever side git calls "ours".
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any

from susanoo.config import ConfigError

_HEADING = re.compile(r"^#{1,6}\s")
_SECTION = re.compile(r"^##\s")


@dataclass(frozen=True)
class KeepBothRule:
    path: str  # fnmatch pattern on the repository-relative POSIX path
    section: re.Pattern[str] | None = None  # the enclosing `## ` heading must match
    lines: re.Pattern[str] | None = None  # every added non-blank line must match
    sort: bool = False  # sort (and de-duplicate) the combined lines

    def matches(self, path: str) -> bool:
        return fnmatch.fnmatchcase(path, self.path)


def parse_rules(raw: Any) -> list[KeepBothRule]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("[[land.keep_both]] must be an array of tables")
    rules: list[KeepBothRule] = []
    for i, item in enumerate(raw):
        where = f"[[land.keep_both]] entry {i + 1}"
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"]:
            raise ConfigError(f"{where}: `path` must be a non-empty string")
        if item.get("enabled") is False:
            continue
        patterns: dict[str, re.Pattern[str] | None] = {}
        for key in ("section", "lines"):
            value = item.get(key)
            if value is None:
                patterns[key] = None
                continue
            if not isinstance(value, str):
                raise ConfigError(f"{where}: `{key}` must be a regular expression string")
            try:
                patterns[key] = re.compile(value)
            except re.error as exc:
                raise ConfigError(f"{where}: invalid `{key}` {value!r} ({exc})") from exc
        if not isinstance(item.get("sort", False), bool):
            raise ConfigError(f"{where}: `sort` must be true or false")
        rules.append(
            KeepBothRule(item["path"], patterns["section"], patterns["lines"], item.get("sort", False))
        )
    return rules


@dataclass
class Resolution:
    path: str
    hunks: int = 0
    resolved: bool = False
    reason: str = ""  # why it was not resolved
    text: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.path, "hunks": self.hunks, "resolved": self.resolved, "reason": self.reason}


@dataclass
class _Hunk:
    ours: list[str]
    base: list[str] | None  # None when the file has no base part (not zdiff3/diff3)
    theirs: list[str]


def _split(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _parse(text: str) -> list[str | _Hunk] | None:
    """Plain lines and hunks, or None when the markers are malformed."""
    items: list[str | _Hunk] = []
    lines = _split(text)
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("<<<<<<< ") and line.rstrip("\r\n") != "<<<<<<<":
            items.append(line)
            i += 1
            continue
        ours: list[str] = []
        base: list[str] | None = None
        theirs: list[str] = []
        part = ours
        i += 1
        while True:
            if i >= len(lines):
                return None
            line = lines[i]
            bare = line.rstrip("\r\n")
            if bare.startswith("|||||||"):
                if part is not ours:
                    return None
                base = []
                part = base
            elif bare == "=======":
                if part is theirs:
                    return None
                part = theirs
            elif bare.startswith(">>>>>>>"):
                if part is not theirs:
                    return None
                items.append(_Hunk(ours, base, theirs))
                i += 1
                break
            elif bare.startswith("<<<<<<<"):
                return None
            else:
                part.append(line)
            i += 1
    return items


def _ensure_newline(lines: list[str], newline: str) -> list[str]:
    if lines and not lines[-1].endswith(("\n", "\r")):
        return [*lines[:-1], lines[-1] + newline]
    return lines


def resolve_text(text: str, path: str, rules: list[KeepBothRule], *, base_side: str) -> Resolution:
    """Resolve every hunk in ``text`` by keeping both sides, or explain why not.

    ``base_side`` is ``"ours"`` or ``"theirs"``: the side that holds the base branch.
    """
    result = Resolution(path=path)
    rule = next((r for r in rules if r.matches(path)), None)
    items = _parse(text)
    if items is None:
        result.reason = "unreadable conflict markers"
        return result
    hunks = [h for h in items if isinstance(h, _Hunk)]
    result.hunks = len(hunks)
    if rule is None:
        result.reason = "not an append-only place"
        return result
    if not hunks:
        result.reason = "no conflict markers (binary, deleted, or renamed file)"
        return result
    newline = "\r\n" if "\r\n" in text else "\n"
    out: list[str] = []
    section: str | None = None
    for item in items:
        if isinstance(item, str):
            if _SECTION.match(item):
                section = item.rstrip("\r\n")
            out.append(item)
            continue
        if item.base is None:
            result.reason = "no base part in the conflict (needs merge.conflictStyle zdiff3)"
            return result
        if any(line.strip() for line in item.base):
            result.reason = "a side changed or removed existing lines"
            return result
        added = [line for line in (*item.ours, *item.theirs) if line.strip()]
        if any(_HEADING.match(line) for line in added):
            result.reason = "a side added a heading"
            return result
        if rule.lines is not None and not all(rule.lines.search(line.rstrip("\r\n")) for line in added):
            result.reason = "an added line is not the kind this place allows"
            return result
        if rule.section is not None and (section is None or not rule.section.search(section)):
            result.reason = f"outside the allowed section ({section or 'no section'})"
            return result
        first, second = (item.ours, item.theirs) if base_side == "ours" else (item.theirs, item.ours)
        combined = _ensure_newline(first, newline) + _ensure_newline(second, newline)
        if rule.sort:
            combined = sorted(dict.fromkeys(combined))
        out.extend(combined)
    result.resolved = True
    result.text = "".join(out)
    return result
