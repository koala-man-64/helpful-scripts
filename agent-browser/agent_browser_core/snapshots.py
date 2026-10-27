from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

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
    for j in range(i + 1, len(lines)):
        if lines[j].level <= lines[i].level:
            return False
        if not lines[j].role.startswith("/"):
            return True
    return False


def _is_field(line: SnapLine, input_values: Mapping[str, Collection[str]]) -> bool:
    """A text-entry line, or a line with a role the page gave an input whose value this line shows."""
    if not line.parsed:
        return False
    if line.role in core_common.INPUT_VALUE_ROLES:
        return True
    values = input_values.get(line.role)
    return bool(values) and line.value is not None and core_common.yaml_unquote(line.value) in values


def input_value_lines(lines: Sequence[SnapLine], input_values: Mapping[str, Collection[str]] | None = None) -> list[SnapLine]:
    """Lines whose value or contents may be what was typed into a field (Playwright prints input values).

    input_values maps a role the page put on a valued <input>/<textarea> to the values such inputs hold."""
    extra = input_values or {}
    return [line for i, line in enumerate(lines) if _is_field(line, extra) and _carries_input(lines, i)]


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


def _shown(line: SnapLine, masked: str | None, hidden: Sequence[str] | None) -> tuple[str | None, str | None]:
    """(name, value) as printed. masked is 'field' for a field whose typed value is hidden (or a line that could not be
    parsed), 'inside' for its contents; hidden lists masked values this line's text may repeat, None for too many."""
    mask = core_common.MASK
    if not line.parsed:
        return None, mask
    if masked == "field":
        return (mask if line.name is not None and hidden is None else _scrub(line.name, hidden or ())), mask if line.value is not None else None
    if (masked == "inside" and not line.role.startswith("/")) or hidden is None:
        return (mask if line.name is not None else None), (mask if line.value is not None else None)
    if line.role == "/url" and line.value is not None:
        return _scrub(line.name, hidden), _scrub(core_common.redact_href(line.value), hidden)
    return _scrub(line.name, hidden), _scrub(line.value, hidden)


def _typed(value: str) -> list[str]:
    """A shown value the way an accessible name repeats it: unquoted and whitespace-normalised like Playwright does,
    plus the JSON-escaped form Playwright prints names in (parse_ai_snapshot only undoes \\")."""
    text = core_common.js_normalize_space(core_common.yaml_unquote(value))
    escaped = json.dumps(text, ensure_ascii=False)[1:-1].replace('\\"', '"')
    return [t for t in dict.fromkeys((text, escaped)) if t]


def _named_parents(lines: Sequence[SnapLine]) -> list[int | None]:
    """For each line, its nearest ancestor that has a name: only a name can repeat a field's value."""
    parents: list[int | None] = []
    stack: list[int] = []
    for i, line in enumerate(lines):
        while stack and lines[stack[-1]].level >= line.level:
            stack.pop()
        k = stack[-1] if stack else None
        parents.append(k if k is None or lines[k].name is not None else parents[k])
        stack.append(i)
    return parents


def _hidden_texts(lines: Sequence[SnapLine], states: Sequence[str | None], secret_refs: Collection[str]) -> list[list[str] | None]:
    """Per line, the masked values its name or value may repeat, longest first; None when the line's name must show
    *** because SCRUB_WORK_CAP ran out.

    A row, cell, button, link or heading takes the values of fields inside it into its accessible name, so every
    masked value, a field's contents included, is removed from the named lines that contain the field. A label
    elsewhere can embed a field too, so a confirmed credential input's own value (SCRUB_MIN characters or more, at
    most SCRUB_PAGE_CAP of them) is removed from every line."""
    named = _named_parents(lines)
    per_line: dict[int, set[str]] = {}
    blind: set[int] = set()  # named lines past the work cap
    work = 0
    everywhere: set[str] = set()
    field = 0
    for i, (line, state) in enumerate(zip(lines, states)):
        if state == "field":
            field = i
        if state not in ("field", "inside") or line.value is None or not line.parsed:
            continue
        texts = _typed(line.value)
        k = named[field]
        while k is not None and k not in blind:
            if work >= core_common.SCRUB_WORK_CAP:
                blind.add(k)  # every named line above it is marked by the same walk, so later walks stop here
            else:
                per_line.setdefault(k, set()).update(texts)
                work += len(texts)
            k = named[k]
        own = i == field or (line.role == "text" and line.level == lines[field].level + 1)  # inline, or after its props
        if own and lines[field].ref in secret_refs and len(everywhere) < core_common.SCRUB_PAGE_CAP:
            everywhere.update(t for t in texts if len(t) >= core_common.SCRUB_MIN)
    page = sorted(everywhere, key=len, reverse=True)
    return [None if i in blind else sorted(per_line[i] | everywhere, key=len, reverse=True) if i in per_line else page
            for i in range(len(lines))]


def _mask_states(lines: Sequence[SnapLine], mask_refs: Collection[str], input_values: Mapping[str, Collection[str]]) -> list[str | None]:
    """A field line is masked when its ref is in mask_refs or it has no ref to check, and so is all it contains.
    A line that could not be parsed is masked the same way: it may be a field."""
    states: list[str | None] = []
    field_level: int | None = None
    for i, line in enumerate(lines):
        if field_level is not None and line.level <= field_level:
            field_level = None
        if field_level is not None:
            states.append("inside")
        elif not line.parsed or (_is_field(line, input_values) and (line.ref is None or line.ref in mask_refs) and _carries_input(lines, i)):
            states.append("field")
            field_level = line.level
        else:
            states.append(None)
    return states


def _display(lines: Sequence[SnapLine], mask_refs: Collection[str], input_values: Mapping[str, Collection[str]],
             secret_refs: Collection[str]) -> list[tuple[str | None, str | None]]:
    states = _mask_states(lines, mask_refs, input_values)
    return [_shown(line, state, hidden) for line, state, hidden in zip(lines, states, _hidden_texts(lines, states, secret_refs))]


def describe_refs(text: str, refs: Collection[str], *, mask_refs: Collection[str] = (),
                  input_values: Mapping[str, Collection[str]] | None = None, secret_refs: Collection[str] = ()) -> dict[str, str]:
    """Role, name and state of each ref's line in a page snapshot, masked as filter_snapshot shows it, without its
    value. A ref missing from the snapshot gets no entry."""
    lines = parse_ai_snapshot(text)
    out: dict[str, str] = {}
    for line, (name, _value) in zip(lines, _display(lines, mask_refs, input_values or {}, secret_refs)):
        if line.ref in refs and line.ref not in out:
            rendered = _render(line, False, None, {}, name, None, cap=200)
            out[line.ref] = rendered[:-1] if rendered.endswith(":") else rendered
    return out


def filter_snapshot(text: str, *, full: bool = False, find: str | None = None, frame_names: dict[str, str] | None = None,
                    mask_refs: Collection[str] = (), input_values: Mapping[str, Collection[str]] | None = None,
                    secret_refs: Collection[str] = ()) -> list[str]:
    """Reduce Playwright's ai-mode tree to what a small model can act on. Pure function.

    Typed values never pass through: a field line (see input_value_lines) whose ref is in mask_refs, or that has no
    ref, shows *** for its value and contents, and its value is removed from the names of the lines around it (and, for
    secret_refs, from every line). /url lines are redacted. --find never matches a masked value or a redacted link.
    """
    lines = parse_ai_snapshot(text)
    prefixes = frame_prefixes(lines)
    shown = _display(lines, mask_refs, input_values or {}, secret_refs)
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
