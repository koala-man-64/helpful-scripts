"""Snapshot pure layer: parse_ai_snapshot, filter_snapshot, and the small helpers around them.

Every input here is either a captured real-Playwright ai-mode snapshot under
tests/fixtures/snapshots/ or a short synthetic string; nothing starts a browser.
"""

from __future__ import annotations

import pytest

import agent_browser as ab

# ---------------------------------------------------------------------------
# parse_ai_snapshot

def test_parse_outer_fixture_line_shapes(outer_text: str) -> None:
    lines = ab.parse_ai_snapshot(outer_text)
    assert len(lines) == 14
    root, heading, link, url, button, iframe, inner_root = lines[:7]

    assert (root.level, root.role, root.name, root.ref, root.attrs, root.value, root.has_children) == (
        0, "generic", None, "e1", ["active"], None, True,
    )
    assert (heading.level, heading.role, heading.name, heading.ref, heading.attrs, heading.value, heading.has_children) == (
        1, "heading", "Outer page", "e2", ["level=1"], None, False,
    )
    assert (link.level, link.role, link.name, link.ref, link.attrs, link.has_children) == (
        1, "link", "Home", "e3", ["cursor=pointer"], True,
    )
    assert (url.level, url.role, url.name, url.ref, url.value, url.has_children) == (2, "/url", None, None, "home.html", False)
    assert (button.level, button.role, button.name, button.ref, button.has_children) == (1, "button", "Outer button", "e4", False)
    assert (iframe.level, iframe.role, iframe.name, iframe.ref, iframe.has_children) == (1, "iframe", None, "e5", True)
    assert (inner_root.level, inner_root.ref, inner_root.prefix) == (2, "f1e1", "f1")  # frame's root wrapper carries the f1 prefix

    save = next(l for l in lines if l.name == "Save")
    assert (save.level, save.role, save.ref, save.has_children) == (3, "button", "f1e4", False)
    paragraph = lines[-1]
    assert (paragraph.role, paragraph.name, paragraph.ref, paragraph.has_children) == ("paragraph", None, None, False)

def test_parse_data_page_fixture_values_and_options(data_page_text: str) -> None:
    lines = ab.parse_ai_snapshot(data_page_text)
    assert len(lines) == 11
    by_ref = {l.ref: l for l in lines if l.ref}

    assert by_ref["e3"].value == "para text" and by_ref["e3"].role == "paragraph"
    clickable = by_ref["e7"]
    assert clickable.role == "generic" and clickable.attrs == ["cursor=pointer"] and clickable.value == "clickable div"
    combobox = by_ref["e9"]
    assert combobox.role == "combobox" and combobox.name == "Pick" and combobox.has_children is True
    textbox = by_ref["e10"]
    assert textbox.role == "textbox" and textbox.name == "Name" and textbox.value == "v"

    options = [l for l in lines if l.role == "option"]
    assert [(o.name, o.attrs) for o in options] == [("a", ["selected"]), ("b", [])]
    assert all(o.ref is None for o in options)

def test_parse_ai_snapshot_unescapes_quoted_names() -> None:
    (line,) = ab.parse_ai_snapshot('- button "Save \\"Draft\\"" [ref=e9]\n')
    assert line.name == 'Save "Draft"' and line.ref == "e9"

def test_parse_ai_snapshot_fallback_line_level_matches_sibling_indent() -> None:
    # Regression: the fallback branch once counted one level per leading space instead of per two.
    text = '- generic [ref=e1]:\n  - button "A" [ref=e2]\n  stray text without a dash\n'
    sibling, stray = ab.parse_ai_snapshot(text)[1:3]
    assert sibling.level == 1  # matched via LINE_RE: 2 spaces // 2
    assert stray.level == sibling.level  # same indent should mean the same level

@pytest.mark.parametrize(
    "target, normalized, ref",
    [
        ("e12", "e12", True),
        ("[ref=e12]", "e12", True),
        ("ref=f2e5", "f2e5", True),
        (" e1 ", "e1", True),
        ("#css", "#css", False),
        ("text=Save", "text=Save", False),
        ("f2e16", "f2e16", True),
    ],
)
def test_is_ref_and_normalize_target(target: str, normalized: str, ref: bool) -> None:
    assert ab.normalize_target(target) == normalized
    assert ab.is_ref(target) is ref

def test_frame_prefixes_maps_iframe_ref_to_child_prefix(outer_text: str) -> None:
    assert ab.frame_prefixes(ab.parse_ai_snapshot(outer_text)) == {"e5": "f1"}
    assert ab.frame_prefixes(ab.parse_ai_snapshot('- button "x" [ref=e1]\n')) == {}

# ---------------------------------------------------------------------------
# filter_snapshot: outer.txt (the interesting fixture - has an iframe)

def test_filter_snapshot_default_outer(outer_text: str) -> None:
    # Golden output also proves: root generic wrapper dropped, /url line dropped, and the
    # link's trailing ":" removed once its only child (/url) was filtered out.
    assert ab.filter_snapshot(outer_text) == [
        '- heading "Outer page" [level=1]',
        '- link "Home" [ref=e3]',
        '- button "Outer button" [ref=e4]',
        "- iframe [ref=e5] (children f1e...):",
        '  - heading "Inner form" [level=2]',
        '  - textbox "Caller" [ref=f1e3]',
        '  - button "Save" [ref=f1e4]',
        '  - button "Reload frame" [ref=f1e5]',
        '  - button "Navigate frame" [ref=f1e6]',
    ]

def test_filter_snapshot_full_keeps_everything(outer_text: str) -> None:
    assert ab.filter_snapshot(outer_text, full=True) == [
        "- generic [active] [ref=e1]:",
        '  - heading "Outer page" [level=1] [ref=e2]',
        '  - link "Home" [ref=e3]:',
        "    - /url: home.html",
        '  - button "Outer button" [ref=e4]',
        "  - iframe [ref=e5] (children f1e...):",
        "    - generic [active] [ref=f1e1]:",
        '      - heading "Inner form" [level=2] [ref=f1e2]',
        "      - text: Caller",
        '      - textbox "Caller" [ref=f1e3]',
        '      - button "Save" [ref=f1e4]',
        '      - button "Reload frame" [ref=f1e5]',
        '      - button "Navigate frame" [ref=f1e6]',
        "      - paragraph",
    ]

def test_filter_snapshot_find_keeps_match_plus_ancestors(outer_text: str) -> None:
    assert ab.filter_snapshot(outer_text, find="Save") == [
        "- iframe [ref=e5] (children f1e...):",
        "  - generic [active]:",
        '    - button "Save" [ref=f1e4]',
    ]
    assert ab.filter_snapshot(outer_text, find="nothing matches this") == []

def test_filter_snapshot_frame_names_labels_the_iframe_line(outer_text: str) -> None:
    out = ab.filter_snapshot(outer_text, frame_names={"e5": "Inner form"})
    assert out[3] == '- iframe "Inner form" [ref=e5] (children f1e...):'
    assert out[0] == '- heading "Outer page" [level=1]'  # rest of the tree is unaffected

# ---------------------------------------------------------------------------
# filter_snapshot: data_page.txt (a combobox with options, a pointer-cursor generic)

def test_filter_snapshot_default_data_page(data_page_text: str) -> None:
    # Golden output also proves: cursor=pointer generic kept, plain generic wrapper (e8)
    # dropped, and option lines under the combobox dropped along with its trailing ":".
    assert ab.filter_snapshot(data_page_text) == [
        '- heading "Title" [level=1]',
        '- button "ok" [ref=e6]',
        "- generic [ref=e7]: clickable div",
        '- combobox "Pick" [ref=e9]',
        '- textbox "Name" [ref=e10]: v',
    ]

def test_filter_snapshot_full_data_page(data_page_text: str) -> None:
    assert ab.filter_snapshot(data_page_text, full=True) == [
        "- generic [active] [ref=e1]:",
        '  - heading "Title" [level=1] [ref=e2]',
        "  - paragraph [ref=e3]: para text",
        '  - button "ok" [ref=e6]',
        "  - generic [ref=e7]: clickable div",
        "  - generic [ref=e8]:",
        "    - text: Pick",
        '    - combobox "Pick" [ref=e9]:',
        '      - option "a" [selected]',
        '      - option "b"',
        '  - textbox "Name" [ref=e10]: v',
    ]

# ---------------------------------------------------------------------------
# budget_lines / diff_lines / find_line

def test_budget_lines_truncation() -> None:
    lines = ["a", "b", "c"]
    assert ab.budget_lines(lines, 100_000) == (lines, False)
    assert ab.budget_lines(lines, 5) == (["a"], True)  # always keeps at least one line
    assert ab.budget_lines([], 1000) == ([], False)

def test_diff_lines() -> None:
    assert ab.diff_lines(None, ["a", "b"]) == {"added": ["a", "b"], "removed": []}
    assert ab.diff_lines(["a", "b", "c"], ["b", "c", "d"]) == {"added": ["d"], "removed": ["a"]}
    capped_after = [f"line{i}" for i in range(ab.CHANGES_CAP + 10)]
    result = ab.diff_lines([], capped_after)
    assert result["added"] == capped_after[: ab.CHANGES_CAP]

def test_find_line(data_page_text: str) -> None:
    out = ab.filter_snapshot(data_page_text)
    assert ab.find_line(out, "e6") == '- button "ok" [ref=e6]'
    assert ab.find_line(out, "e999") is None
    assert ab.find_line(None, "e1") is None

# ---------------------------------------------------------------------------
# sanitize_text / redact_url / safe_name

def test_sanitize_text_strips_control_bidi_zero_width_and_caps() -> None:
    assert ab.sanitize_text("a\x00b\x1fc\u200bd\u202ee", cap=300) == "abcde"
    assert ab.sanitize_text("x" * 10, cap=5) == "xxxxx..."
    assert ab.sanitize_text("x" * 10, cap=0) == "x" * 10  # cap=0 means uncapped

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.com/path?code=ABC&token=xyz&foo=bar#frag", "https://example.com/path?code=***&token=***&foo=bar"),  # code/token masked, fragment dropped
        ("https://x.com/a?SAMLResponse=abc&state=def&keep=me", "https://x.com/a?SAMLResponse=***&state=***&keep=me"),  # case-insensitive key match, other params kept
        ("https://example.com/path", "https://example.com/path"),
        ("", ""),
        (None, ""),
        ("not a url at all", "not a url at all"),  # non-URL input survives
    ],
)
def test_redact_url_table(url: str | None, expected: str) -> None:
    assert ab.redact_url(url) == expected

@pytest.mark.parametrize(
    "name, expected",
    [
        ("../../etc/passwd", "passwd"),
        ("normal-file_name.txt", "normal-file_name.txt"),
        ("C:\\Windows\\evil<>:.exe", "evil_.exe"),
        ("", "download"),
        (None, "download"),
    ],
)
def test_safe_name_table(name: object, expected: str) -> None:
    assert ab.safe_name(name) == expected

def test_safe_name_caps_length() -> None:
    assert len(ab.safe_name("a" * 500)) == 120

# ---------------------------------------------------------------------------
# is_secret_field

@pytest.mark.parametrize(
    "info, url, title, expected",
    [
        ({"type": "password"}, "", "", True),
        ({"autocomplete": "one-time-code"}, "", "", True),
        ({"text": "Enter your password below"}, "", "", True),  # strong word, any page
        ({"text": "security code"}, "", "", True),  # strong phrase, any page
        ({"text": "pin"}, "", "", False),  # weak word, NOT a login page
        ({"text": "pin"}, "https://x.com/login", "", True),  # weak word, login page (url)
        ({"text": "code"}, "https://x.com/signin", "", True),  # weak word, login page
        ({"text": "code"}, "", "Sign-in to Contoso", True),  # weak word, login page (title)
        ({"inputmode": "numeric", "maxlength": 6, "text": ""}, "https://x.com/mfa", "", True),
        ({"inputmode": "numeric", "maxlength": 6, "text": ""}, "https://x.com/normal", "", False),
        ({"inputmode": "numeric", "maxlength": 20, "text": ""}, "https://x.com/mfa", "", False),  # too long
        ({"text": "username"}, "", "", False),
        ({}, "", "", False),
        (None, "", "", False),
    ],
)
def test_is_secret_field_matrix(info: dict | None, url: str, title: str, expected: bool) -> None:
    assert ab.is_secret_field(info, url, title) is expected
