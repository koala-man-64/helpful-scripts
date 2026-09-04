"""Offline tests for github_keyword_search.

Every test replaces the gh subprocess seam (``scanner._run_gh``) with a
scripted fake and the sleep hook (``scanner._sleep``) with a recorder, so no
test touches the network, the gh binary, or real time.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import github_keyword_search as gks


def ok(payload) -> tuple[int, str, str]:
    return (0, json.dumps(payload), "")


def code_item(
    repo: str = "acme/app",
    path: str = "src/config.py",
    sha: str = "b" * 40,
    fragment: str = "API_KEY = 'super secret token here'",
) -> dict:
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "sha": sha,
        "html_url": f"https://github.com/{repo}/blob/{'c' * 40}/{path}",
        "repository": {"full_name": repo},
        "text_matches": [{"fragment": fragment}],
    }


def commit_item(
    sha: str = "c" * 40,
    message: str = "fix: rotate secret\n\nlong body",
    committer_name: str = "Alice Example",
    day: str = "2024-05-01",
) -> dict:
    stamp = f"{day}T10:00:00Z"
    return {
        "sha": sha,
        "commit": {
            "message": message,
            "committer": {"name": committer_name, "date": stamp},
        },
    }


class FakeGh:
    """Scripted stand-in for gks._run_gh.

    ``routes`` is an ordered list of (substring, response) pairs matched
    against the request path (the last positional argv entry); a response
    given as a list serves successive calls (the last entry repeats).
    Unmatched search paths return an empty result set and anything else
    returns an empty object.
    """

    def __init__(self, routes: list[tuple[str, object]] | None = None) -> None:
        self.routes = [[key, value] for key, value in (routes or [])]
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        assert argv[0] == "api"
        path = argv[-1]
        self.calls.append(argv)
        for route in self.routes:
            key, value = route
            if key in path:
                if isinstance(value, list):
                    return value.pop(0) if len(value) > 1 else value[0]
                return value
        if path.startswith("search/"):
            return ok({"total_count": 0, "items": []})
        return ok({})


class HelperTestCase(unittest.TestCase):
    def test_build_scopes_unions_orgs_and_repos(self) -> None:
        self.assertEqual(
            gks.build_scopes(["acme", "beta"], ["acme/app"]),
            ["org:acme", "org:beta", "repo:acme/app"],
        )

    def test_extract_snippet_collapses_whitespace_and_joins_fragments(self) -> None:
        item = {"text_matches": [{"fragment": "line one\nline  two"}, {"fragment": "second match"}]}
        self.assertEqual(gks.extract_snippet(item), "line one line two … second match")

    def test_extract_snippet_truncates_long_text(self) -> None:
        item = {"text_matches": [{"fragment": "x" * 500}]}
        snippet = gks.extract_snippet(item, max_len=20)
        self.assertEqual(len(snippet), 20)
        self.assertTrue(snippet.endswith("…"))

    def test_extract_snippet_handles_no_text_matches(self) -> None:
        self.assertEqual(gks.extract_snippet({}), "")

    def test_build_row_uses_search_html_url_and_commit_metadata(self) -> None:
        item = code_item()
        commit = commit_item()
        row = gks.build_row("secret", "acme/app", "src/config.py", "main", commit, item)
        self.assertEqual(row.keyword, "secret")
        self.assertEqual(row.repo, "acme/app")
        self.assertEqual(row.commit_sha, "c" * 40)
        self.assertEqual(row.commit_message, "fix: rotate secret")
        self.assertEqual(row.committer, "Alice Example")
        self.assertEqual(row.commit_date, "2024-05-01T10:00:00Z")
        self.assertEqual(row.url, item["html_url"])
        self.assertIn("API_KEY", row.snippet)

    def test_build_row_blank_commit_fields_when_commit_lookup_failed(self) -> None:
        item = code_item()
        row = gks.build_row("secret", "acme/app", "src/config.py", "main", None, item)
        self.assertEqual(row.commit_sha, "")
        self.assertEqual(row.commit_message, "")
        self.assertEqual(row.committer, "")
        self.assertEqual(row.commit_date, "")
        # Falls back to the search result's own URL even without a commit.
        self.assertEqual(row.url, item["html_url"])

    def test_parse_repo_rejects_bad_shape(self) -> None:
        with self.assertRaises(Exception):
            gks.parse_repo("not-a-repo")
        self.assertEqual(gks.parse_repo("acme/app"), "acme/app")


class GhApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        patcher = mock.patch.object(gks, "_sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_gh(self, fake: FakeGh) -> FakeGh:
        patcher = mock.patch.object(gks, "_run_gh", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_search_code_sends_text_match_header(self) -> None:
        fake = self.use_gh(FakeGh([("search/code", ok({"total_count": 1, "items": [code_item()]}))]))
        gks.search_keyword("secret", "org:acme", [])
        argv = fake.calls[0]
        self.assertIn("-H", argv)
        self.assertIn(gks.TEXT_MATCH_ACCEPT_HEADER, argv)
        # One search call sleeps once at the code-search rate.
        self.assertEqual(self.sleeps, [gks.SEARCH_CODE_SLEEP_SECONDS])

    def test_search_code_warns_when_total_exceeds_cap(self) -> None:
        page = [code_item(path=f"f{i}.py") for i in range(gks.SEARCH_PAGE_SIZE)]
        fake = self.use_gh(
            FakeGh([("search/code", [ok({"total_count": 1500, "items": page})] * gks.MAX_SEARCH_PAGES)])
        )
        warnings: list[str] = []
        items = gks.search_keyword("secret", "org:acme", warnings)
        self.assertEqual(len(items), gks.MAX_SEARCH_RESULTS)
        self.assertTrue(any("1500 matches exceeds" in w for w in warnings))

    def test_repo_default_branch_is_cached(self) -> None:
        fake = self.use_gh(FakeGh([("repos/acme/app", ok({"default_branch": "main"}))]))
        cache: dict[str, str] = {}
        warnings: list[str] = []
        first = gks.repo_default_branch("acme/app", cache, warnings)
        second = gks.repo_default_branch("acme/app", cache, warnings)
        self.assertEqual(first, "main")
        self.assertEqual(second, "main")
        self.assertEqual(len(fake.calls), 1)  # second call served from cache

    def test_repo_default_branch_warns_on_failure(self) -> None:
        self.use_gh(FakeGh([("repos/acme/app", (1, "", "HTTP 404: Not Found"))]))
        cache: dict[str, str] = {}
        warnings: list[str] = []
        branch = gks.repo_default_branch("acme/app", cache, warnings)
        self.assertEqual(branch, "")
        self.assertTrue(any("could not fetch default branch" in w for w in warnings))

    def test_latest_commit_for_path_caches_and_handles_empty_history(self) -> None:
        fake = self.use_gh(
            FakeGh(
                [
                    ("commits?path=src%2Fconfig.py", ok([commit_item()])),
                    ("commits?path=src%2Fempty.py", ok([])),
                ]
            )
        )
        cache: dict[tuple[str, str], dict | None] = {}
        warnings: list[str] = []
        commit = gks.latest_commit_for_path("acme/app", "src/config.py", "main", cache, warnings)
        self.assertIsNotNone(commit)
        gks.latest_commit_for_path("acme/app", "src/config.py", "main", cache, warnings)
        self.assertEqual(len(fake.calls), 1)  # cached on second lookup

        empty = gks.latest_commit_for_path("acme/app", "src/empty.py", "main", cache, warnings)
        self.assertIsNone(empty)


class RenderReportTestCase(unittest.TestCase):
    def test_escapes_untrusted_content(self) -> None:
        row = gks.MatchRow(
            keyword='<script>alert(1)</script>',
            repo="acme/app",
            path="src/x.py",
            url="https://github.com/acme/app",
            branch="main",
            commit_sha="d" * 40,
            commit_message="<img src=x onerror=alert(1)>",
            committer="Mallory",
            commit_date="2024-05-01T00:00:00Z",
            snippet="payload: <script>evil()</script>",
        )
        report = gks.render_report(["<script>"], ["org:acme"], [row], [], "2024-05-01 00:00 UTC")
        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertNotIn("<img src=x onerror=alert(1)>", report)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", report)
        self.assertIn("acme/app", report)

    def test_includes_warnings_and_summary_counts(self) -> None:
        rows = [
            gks.MatchRow("secret", "acme/app", "a.py", "u1", "main", "a" * 40, "m", "c", "d", "s"),
            gks.MatchRow("secret", "acme/web", "b.py", "u2", "main", "b" * 40, "m", "c", "d", "s"),
        ]
        report = gks.render_report(["secret"], ["org:acme"], rows, ["oops"], "now")
        self.assertIn("1 warning(s)", report)
        self.assertIn("oops", report)
        self.assertIn("2 match(es) across 2 repo(s)", report)


class MainTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        patcher = mock.patch.object(gks, "_sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_gh(self, fake: FakeGh) -> FakeGh:
        patcher = mock.patch.object(gks, "_run_gh", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gks.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_requires_org_or_repo(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_main(["secret"])
        self.assertEqual(ctx.exception.code, 2)

    def test_requires_a_keyword(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_main(["--org", "acme"])
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_keyword_with_double_quote(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_main(['bad"keyword', "--org", "acme"])
        self.assertEqual(ctx.exception.code, 2)

    def test_end_to_end_writes_report(self) -> None:
        self.use_gh(
            FakeGh(
                [
                    ("rate_limit", ok({})),
                    ("search/code", ok({"total_count": 1, "items": [code_item()]})),
                    ("repos/acme/app", ok({"default_branch": "main"})),
                    ("commits?path=", ok([commit_item()])),
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            code, stdout, stderr = self.run_main(
                ["secret", "--org", "acme", "--out-dir", str(out_dir)]
            )
            self.assertEqual(code, 0, stderr)
            report_path = out_dir / "keyword_search_report.html"
            self.assertTrue(report_path.exists())
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("acme/app", content)
            self.assertIn("secret", content)
            self.assertIn("found 1 match(es) across 1 repo(s)", stdout)

    def test_strict_exits_2_on_warnings(self) -> None:
        # A match with --max-files=0 forces the "reached" warning path.
        self.use_gh(
            FakeGh(
                [
                    ("rate_limit", ok({})),
                    ("search/code", ok({"total_count": 1, "items": [code_item()]})),
                    ("repos/acme/app", ok({"default_branch": "main"})),
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            code, _, stderr = self.run_main(
                [
                    "secret",
                    "--org",
                    "acme",
                    "--out-dir",
                    str(out_dir),
                    "--max-files",
                    "0",
                    "--strict",
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("--max-files=0 reached", stderr)

    def test_gh_missing_returns_1(self) -> None:
        def missing(argv: list[str]) -> tuple[int, str, str]:
            raise gks.GhError("gh not found")

        self.use_gh(missing)
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_main(
                ["secret", "--org", "acme", "--out-dir", tmp]
            )
            self.assertEqual(code, 1)
            self.assertIn("gh not found", stderr)


if __name__ == "__main__":
    unittest.main()
