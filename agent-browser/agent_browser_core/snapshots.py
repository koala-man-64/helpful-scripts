from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from . import common as core_common


@dataclass
class SnapLine:
    level: int
    role: str
    name: str | None
    ref: str | None
    attrs: list[str]
    value: str | None
    has_children: bool
    raw: str

    @property
    def prefix(self) -> str:
        m = core_common.REF_RE.match(self.ref or "")
        return (m.group(1) or "") if m else ""


def parse_ai_snapshot(text: str) -> list[SnapLine]:
    lines: list[SnapLine] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = core_common.LINE_RE.match(raw)
        if not m:
            level = (len(raw) - len(raw.lstrip(" "))) // 2
            lines.append(SnapLine(level, "text", None, None, [], raw.strip(), False, raw))
            continue
        attrs = re.findall(r"\[([^\]]*)\]", m.group("attrs") or "")
        ref = next((a[4:] for a in attrs if a.startswith("ref=")), None)
        attrs = [a for a in attrs if not a.startswith("ref=")]
        name = m.group("name")
        if name is not None:
            name = name.replace('\\"', '"')
        value = m.group("value")
        lines.append(SnapLine(len(m.group("indent")) // 2, m.group("role"), name, ref, attrs, value, bool(m.group("colon")) and value is None, raw))
    return lines


def is_ref(target: str) -> bool:
    return bool(core_common.REF_RE.match(normalize_target(target)))


def normalize_target(target: str) -> str:
    t = (target or "").strip()
    t = re.sub(r"^\[ref=(.+)\]$", r"\1", t)
    t = re.sub(r"^ref=", "", t)
    return t


def frame_prefixes(lines: Sequence[SnapLine]) -> dict[str, str]:
    """Map each iframe line's ref to the `f<N>` prefix its children carry."""
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        if line.role != "iframe" or not line.ref:
            continue
        for child in lines[i + 1:]:
            if child.level <= line.level:
                break
            if child.ref and child.prefix:
                out[line.ref] = child.prefix
                break
    return out


def _render(line: SnapLine, keep_ref: bool, frame_names: dict[str, str] | None, prefixes: dict[str, str]) -> str:
    attrs = [a for a in line.attrs if a.split("=")[0] in core_common.KEEP_ATTRS or a.startswith("level=")]
    parts = ["- " + line.role]
    if line.role == "iframe" and line.ref:
        label = (frame_names or {}).get(line.ref)
        if label:
            parts.append(f'"{core_common.sanitize_text(label, 80)}"')
    elif line.name is not None:
        parts.append('"' + core_common.sanitize_text(line.name, core_common.LINE_CHAR_CAP).replace('"', '\\"') + '"')
    parts.extend(f"[{a}]" for a in attrs)
    if keep_ref and line.ref:
        parts.append(f"[ref={line.ref}]")
    if line.role == "iframe" and line.ref and line.ref in prefixes:
        parts.append(f"(children {prefixes[line.ref]}e...)")
    text = " ".join(parts)
    if line.value is not None and line.role != "iframe":
        text += ": " + core_common.sanitize_text(line.value, core_common.LINE_CHAR_CAP)
    elif line.has_children:
        text += ":"
    return text


def filter_snapshot(text: str, *, full: bool = False, find: str | None = None, frame_names: dict[str, str] | None = None) -> list[str]:
    """Reduce Playwright's ai-mode tree to what a small model can act on. Pure function."""
    lines = parse_ai_snapshot(text)
    prefixes = frame_prefixes(lines)
    needle = (find or "").strip().lower()
    keep: list[tuple[bool, bool]] = []  # (keep line, keep ref)
    stack: list[tuple[int, str]] = []   # (level, role) of ancestors
    parent_roles: list[str | None] = []
    for line in lines:
        while stack and stack[-1][0] >= line.level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        parent_roles.append(parent)
        stack.append((line.level, line.role))
        if full:
            keep.append((True, bool(line.ref)))
            continue
        role = line.role
        actionable = role in core_common.ACTIONABLE_ROLES or (role == "generic" and "cursor=pointer" in line.attrs)
        context = role in core_common.CONTEXT_ROLES or (role in core_common.NAMED_CONTEXT_ROLES and bool(line.name))
        option = role == "option" and parent in core_common.OPTION_PARENTS
        if needle:
            match = needle in line.raw.lower()
            keep.append((match, actionable and match))
            continue
        keep.append((actionable or context or option, actionable))
    if needle and not full:
        # keep ancestors of every match so the model sees where it sits
        for i in range(len(lines) - 1, -1, -1):
            if keep[i][0]:
                level = lines[i].level
                for j in range(i - 1, -1, -1):
                    if lines[j].level < level:
                        if not keep[j][0]:
                            keep[j] = (True, lines[j].role in core_common.ACTIONABLE_ROLES or (lines[j].role == "generic" and "cursor=pointer" in lines[j].attrs))
                        level = lines[j].level
                        if level == 0:
                            break
    out: list[str] = []
    kept_levels: list[int] = []
    for line, (keep_line, keep_ref) in zip(lines, keep):
        while kept_levels and kept_levels[-1] >= line.level:
            kept_levels.pop()
        if not keep_line:
            continue
        if line.role in ("text", "/url", "paragraph") and not full and not needle:
            continue
        depth = len(kept_levels)
        kept_levels.append(line.level)
        if line.role == "generic" and line.level == 0 and not full:
            kept_levels.pop()  # the root wrapper never renders; its children stay at depth 0
            continue
        out.append("  " * depth + _render(line, keep_ref, frame_names, prefixes))
    # A trailing colon promises children; drop it when the filter removed them all.
    for i, text in enumerate(out):
        if text.endswith(":"):
            depth = len(text) - len(text.lstrip(" "))
            has_child = i + 1 < len(out) and (len(out[i + 1]) - len(out[i + 1].lstrip(" "))) > depth
            if not has_child:
                out[i] = text[:-1]
    return out


def budget_lines(lines: Sequence[str], max_bytes: int) -> tuple[list[str], bool]:
    used = 0
    kept: list[str] = []
    for line in lines:
        cost = len(json.dumps(line, ensure_ascii=False)) + 6
        if used + cost > max_bytes and kept:
            return kept, True
        used += cost
        kept.append(line)
    return kept, False


def diff_lines(before: Sequence[str] | None, after: Sequence[str]) -> dict[str, list[str]]:
    if before is None:
        return {"added": list(after)[:core_common.CHANGES_CAP], "removed": []}
    before_set, after_set = set(before), set(after)
    added = [l for l in after if l not in before_set][:core_common.CHANGES_CAP]
    removed = [l for l in before if l not in after_set][:core_common.CHANGES_CAP]
    return {"added": added, "removed": removed}


def find_line(lines: Sequence[str] | None, ref: str) -> str | None:
    marker = f"[ref={ref}]"
    for line in lines or []:
        if marker in line:
            return line.strip()
    return None
