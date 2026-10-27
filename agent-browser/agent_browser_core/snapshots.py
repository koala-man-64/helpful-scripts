from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Iterable, Sequence

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
    parsed: bool = True  # False: an unrecognised line kept as text; its content is never shown

    @property
    def prefix(self) -> str:
        m = core_common.REF_RE.match(self.ref or "")
        return (m.group(1) or "") if m else ""


def _key_match(raw: str) -> tuple[re.Match[str], re.Match[str]] | None:
    """(line match, key match) for a plain or single-quoted key, or None."""
    m = core_common.LINE_RE.match(raw)
    if m:
        return m, m
    q = core_common.QUOTED_LINE_RE.match(raw)
    key = core_common.SNAP_KEY_RE.match(q.group("key").replace("''", "'")) if q else None
    return (q, key) if key else None


def parse_ai_snapshot(text: str) -> list[SnapLine]:
    lines: list[SnapLine] = []
    # Split on \n only: splitlines() also breaks on U+2028, U+0085 and others that Playwright leaves raw in hrefs
    # and names, which would let a page forge a whole snapshot line.
    for raw in text.split("\n"):
        raw = raw.removesuffix("\r")
        if not raw.strip():
            continue
        match = _key_match(raw)
        if not match:
            level = (len(raw) - len(raw.lstrip(" "))) // 2
            lines.append(SnapLine(level, "text", None, None, [], raw.strip(), False, raw, parsed=False))
            continue
        m, key = match
        attrs = re.findall(r"\[([^\]]*)\]", key.group("attrs") or "")
        ref = next((a[4:] for a in attrs if a.startswith("ref=")), None)
        attrs = [a for a in attrs if not a.startswith("ref=")]
        name = key.group("name")
        if name is not None:
            name = name.replace('\\"', '"')
        else:
            name = key.group("rname")
        value = m.group("value")
        lines.append(SnapLine(len(m.group("indent")) // 2, key.group("role"), name, ref, attrs, value, bool(m.group("colon")) and value is None, raw))
    return lines


def _carries_input(lines: Sequence[SnapLine], i: int) -> bool:
    """Whether line i shows something typed: a value, or any child beyond its /placeholder-style props."""
    if lines[i].value is not None:
        return True
    for child in lines[i + 1:]:
        if child.level <= lines[i].level:
            return False
        if not child.role.startswith("/"):
            return True
    return False


def input_value_lines(lines: Sequence[SnapLine], roles: Collection[str] = core_common.INPUT_VALUE_ROLES) -> list[SnapLine]:
    """Lines whose value or contents may be what was typed into a field (Playwright prints input values)."""
    return [line for i, line in enumerate(lines) if line.role in roles and line.parsed and _carries_input(lines, i)]


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


def _render(line: SnapLine, keep_ref: bool, frame_names: dict[str, str] | None, prefixes: dict[str, str],
            name: str | None, value: str | None, cap: int = core_common.LINE_CHAR_CAP) -> str:
    attrs = [a for a in line.attrs if a.split("=")[0] in core_common.KEEP_ATTRS or a.startswith("level=")]
    parts = ["- " + line.role]
    if line.role == "iframe" and line.ref:
        label = (frame_names or {}).get(line.ref)
        if label:
            parts.append(f'"{core_common.sanitize_text(label, 80)}"')
    elif name is not None:
        parts.append('"' + core_common.sanitize_text(name, cap).replace('"', '\\"') + '"')
    parts.extend(f"[{a}]" for a in attrs)
    if keep_ref and line.ref:
        parts.append(f"[ref={line.ref}]")
    if line.role == "iframe" and line.ref and line.ref in prefixes:
        parts.append(f"(children {prefixes[line.ref]}e...)")
    text = " ".join(parts)
    if value is not None and line.role != "iframe":
        text += ": " + core_common.sanitize_text(value, cap)
    elif line.has_children:
        text += ":"
    return text.translate(core_common.LINE_BREAKS)


def _scrub(text: str | None, hidden: Sequence[str]) -> str | None:
    if text is None:
        return None
    for secret in hidden:
        text = text.replace(secret, core_common.MASK)
    return text


def _shown(line: SnapLine, masked: str | None, hidden: Sequence[str]) -> tuple[str | None, str | None]:
    """(name, value) as printed. masked is 'field' for a field whose typed value is hidden, 'inside' for its contents;
    hidden lists masked values this line's text may repeat."""
    mask = core_common.MASK
    if not line.parsed:
        return None, mask
    if masked == "field":
        return _scrub(line.name, hidden), mask if line.value is not None else None
    if masked == "inside" and not line.role.startswith("/"):
        return (mask if line.name is not None else None), (mask if line.value is not None else None)
    if line.role == "/url" and line.value is not None:
        return _scrub(line.name, hidden), _scrub(core_common.redact_href(line.value), hidden)
    return _scrub(line.name, hidden), _scrub(line.value, hidden)


def _typed(value: str) -> list[str]:
    """A shown value the way an accessible name repeats it: unquoted, whitespace collapsed, and JSON-escaped as
    Playwright prints names (parse_ai_snapshot only undoes \\")."""
    text = " ".join(core_common.yaml_unquote(value).replace("​", "").replace("­", "").split())
    escaped = json.dumps(text, ensure_ascii=False)[1:-1].replace('\\"', '"')
    return [t for t in dict.fromkeys((text, escaped)) if t]


def _parents(lines: Sequence[SnapLine]) -> list[int | None]:
    parents: list[int | None] = []
    stack: list[int] = []
    for i, line in enumerate(lines):
        while stack and lines[stack[-1]].level >= line.level:
            stack.pop()
        parents.append(stack[-1] if stack else None)
        stack.append(i)
    return parents


def _hidden_texts(lines: Sequence[SnapLine], states: Sequence[str | None], secret_refs: Collection[str]) -> list[list[str]]:
    """Per line, the masked values its name or value may repeat. A row, cell, button, link or heading takes the values
    of fields inside it into its accessible name, and a label can embed a field elsewhere. So a masked value is removed
    from every line that contains its field, and a confirmed credential's value of SCRUB_MIN or more characters from
    every line."""
    parents = _parents(lines)
    per_line: list[set[str]] = [set() for _ in lines]
    everywhere: set[str] = set()
    field = 0
    for i, (line, state) in enumerate(zip(lines, states)):
        if state == "field":
            field = i
        if state not in ("field", "inside") or line.value is None or not line.parsed:
            continue
        for text in _typed(line.value):
            k = parents[field]
            while k is not None:
                per_line[k].add(text)
                k = parents[k]
            if lines[field].ref in secret_refs and len(text) >= core_common.SCRUB_MIN:
                everywhere.add(text)
    return [sorted(texts | everywhere, key=len, reverse=True) for texts in per_line]


def _mask_states(lines: Sequence[SnapLine], mask_refs: Collection[str], value_roles: Collection[str]) -> list[str | None]:
    """A valued input line is masked when its ref is in mask_refs or it has no ref to check; so is all it contains."""
    states: list[str | None] = []
    field_level: int | None = None
    for i, line in enumerate(lines):
        if field_level is not None and line.level <= field_level:
            field_level = None
        if field_level is not None:
            states.append("inside")
        elif line.role in value_roles and line.parsed and (line.ref is None or line.ref in mask_refs) and _carries_input(lines, i):
            states.append("field")
            field_level = line.level
        else:
            states.append(None)
    return states


def describe_line(text: str) -> str:
    """The first line of an element's own snapshot without its value: role, name and state. Nothing in it was
    checked, so the values of every field inside are kept out of the name."""
    lines = parse_ai_snapshot(text)
    if not lines or not lines[0].parsed:
        return ""
    states = _mask_states(lines, {line.ref for line in lines if line.ref}, core_common.INPUT_VALUE_ROLES)
    name, _value = _shown(lines[0], None, _hidden_texts(lines, states, ())[0])
    rendered = _render(lines[0], False, None, {}, name, None, cap=200)
    return rendered[:-1] if rendered.endswith(":") else rendered


def filter_snapshot(text: str, *, full: bool = False, find: str | None = None, frame_names: dict[str, str] | None = None,
                    mask_refs: Collection[str] = (), value_roles: Collection[str] = core_common.INPUT_VALUE_ROLES,
                    secret_refs: Collection[str] = ()) -> list[str]:
    """Reduce Playwright's ai-mode tree to what a small model can act on. Pure function.

    Typed values never pass through: an input-role line in value_roles whose ref is in mask_refs, or that has no ref,
    shows *** for its value and contents, and its value is removed from the names of the lines around it (and, for
    secret_refs, from every line). /url lines are redacted. --find matches only what is shown.
    """
    lines = parse_ai_snapshot(text)
    prefixes = frame_prefixes(lines)
    states = _mask_states(lines, mask_refs, value_roles)
    shown = [_shown(line, state, hidden) for line, state, hidden in zip(lines, states, _hidden_texts(lines, states, secret_refs))]
    needle = (find or "").strip().lower()
    keep: list[tuple[bool, bool]] = []  # (keep line, keep ref)
    stack: list[tuple[int, str]] = []   # (level, role) of ancestors
    parent_roles: list[str | None] = []
    for line, (name, value) in zip(lines, shown):
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
            match = needle in _render(line, True, frame_names, prefixes, name, value, cap=0).lower()
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
    for line, (keep_line, keep_ref), (name, value) in zip(lines, keep, shown):
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
        out.append("  " * depth + _render(line, keep_ref, frame_names, prefixes, name, value))
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
        cost = len(json.dumps(line, ensure_ascii=False).encode("utf-8")) + 6
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
