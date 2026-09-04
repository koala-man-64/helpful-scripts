"""Offline tests for github_keyword_search.

Every test replaces the gh subprocess seam (``gks._run_gh``) with a scripted
fake and the sleep hook (``gks._sleep``) with a recorder, so no test touches
the network, the gh binary, or real time. Config is built explicitly per
test (never via the module-level constants), so tests can't leak state into
each other or depend on someone's local edits to the CONFIGURATION block.
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


def repo(name: str = "app", org: str = "acme", fork: bool = False, branch: str = "main") -> dict:
    return {
        "name": name,
        "full_name": f"{org}/{name}",
        "fork": fork,
        "default_branch": branch,
    }


def commit_summary(
    sha: str = "a" * 40,
    repo_full_name: str = "acme/app",
    message: str = "fix: rotate secret",
    committer_name: str = "Alice Example",
    day: str = "2024-05-01",
) -> dict:
    stamp = f"{day}T10:00:00Z"
    return {
        "sha": sha,
        "html_url": f"https://github.com/{repo_full_name}/commit/{sha}",
        "commit": {
            "message": message,
            "committer": {"name": committer_name, "date": stamp},
        },
    }


def commit_detail(sha: str = "a" * 40, files: list[dict] | None = None) -> dict:
    return {"sha": sha, "files": files or []}


def file_entry(filename: str = "src/config.py", patch: str | None = None) -> dict:
    return {"filename": filename, "patch": patch}


class FakeGh:
    """Scripted stand-in for gks._run_gh.

    ``routes`` is an ordered list of (substring, response) pairs matched
    against the request path (the last positional argv entry); a response
    given as a list serves successive calls (the last entry repeats).
    Unmatched paths return an empty object.
    """

    def __init__(self, routes: list[tuple[str, object]] | None = None) -> None:
        self.routes = [[key, value] for key, value in (routes or [])]
        self.calls: list[str] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        assert argv[0] == "api"
        path = argv[-1]
        self.calls.append(path)
        for route in self.routes:
            key, value = route
            if key in path:
                if isinstance(value, list):
                    return value.pop(0) if len(value) > 1 else value[0]
                return value
        return ok({})


class ConfigTestCase(unittest.TestCase):
    def test_requires_org(self) -> None:
        cfg = gks.Config(org="  ", keywords=("secret",))
        self.assertTrue(any("GITHUB_ORG" in e for e in cfg.validate()))

    def test_requires_keywords(self) -> None:
        cfg = gks.Config(org="acme", keywords=())
        self.assertTrue(any("KEYWORDS" in e for e in cfg.validate()))

    def test_rejects_bad_dates(self) -> None:
        cfg = gks.Config(org="acme", keywords=("secret",), since="not-a-date")
        self.assertTrue(any("SINCE" in e for e in cfg.validate()))

    def test_rejects_since_after_until(self) -> None:
        cfg = gks.Config(org="acme", keywords=("secret",), since="2024-06-01", until="2024-01-01")
        self.assertTrue(any("is after" in e for e in cfg.validate()))

    def test_valid_config_has_no_errors(self) -> None:
        cfg = gks.Config(org="acme", keywords=("secret",), since="2024-01-01", until="2024-06-01")
        self.assertEqual(cfg.validate(), [])

    def test_default_config_reads_module_constants(self) -> None:
        with mock.patch.object(gks, "GITHUB_ORG", "from-constants"), mock.patch.object(
            gks, "KEYWORDS", ("ctor-keyword",)
        ):
            cfg = gks.default_config()
        self.assertEqual(cfg.org, "from-constants")
        self.assertEqual(cfg.keywords, ("ctor-keyword",))


class FilterReposTestCase(unittest.TestCase):
    def test_excludes_forks_by_default(self) -> None:
        repos = [repo("app"), repo("forked", fork=True)]
        result = gks.filter_repos(repos, (), include_forks=False)
        self.assertEqual([r["name"] for r in result], ["app"])

    def test_include_forks_keeps_them(self) -> None:
        repos = [repo("app"), repo("forked", fork=True)]
        result = gks.filter_repos(repos, (), include_forks=True)
        self.assertEqual({r["name"] for r in result}, {"app", "forked"})

    def test_name_keyword_filter_is_case_insensitive_substring(self) -> None:
        repos = [repo("Payments-Service"), repo("infra-tools"), repo("web-app")]
        result = gks.filter_repos(repos, ("payments",), include_forks=False)
        self.assertEqual([r["name"] for r in result], ["Payments-Service"])

    def test_empty_keyword_filter_keeps_everything(self) -> None:
        repos = [repo("a"), repo("b")]
        result = gks.filter_repos(repos, (), include_forks=False)
        self.assertEqual(len(result), 2)


class SnippetTestCase(unittest.TestCase):
    def test_message_snippet_centers_on_keyword(self) -> None:
        message = "x" * 100 + "SECRET_TOKEN" + "y" * 100
        snippet = gks.extract_message_snippet(message, "SECRET_TOKEN")
        self.assertIn("SECRET_TOKEN", snippet)
        self.assertTrue(snippet.startswith("…"))
        self.assertTrue(snippet.endswith("…"))

    def test_message_snippet_no_ellipsis_when_short(self) -> None:
        message = "found the SECRET_TOKEN here"
        snippet = gks.extract_message_snippet(message, "SECRET_TOKEN")
        self.assertEqual(snippet, message)

    def test_clean_diff_line_marks_added_and_removed(self) -> None:
        self.assertEqual(gks.clean_diff_line("+  api_key = 'x'"), "+ api_key = 'x'")
        self.assertEqual(gks.clean_diff_line("-  api_key = 'x'"), "- api_key = 'x'")

    def test_clean_diff_line_truncates(self) -> None:
        long_line = "+" + "z" * 500
        cleaned = gks.clean_diff_line(long_line)
        self.assertEqual(len(cleaned), gks.SNIPPET_MAX_LEN)
        self.assertTrue(cleaned.endswith("…"))


class FindDiffMatchTestCase(unittest.TestCase):
    def test_finds_match_in_added_line(self) -> None:
        detail = commit_detail(files=[file_entry(patch="@@ -1,2 +1,3 @@\n+API_KEY = 'abc'\n context")])
        result = gks.find_diff_match("API_KEY", detail)
        self.assertIsNotNone(result)
        filename, snippet = result
        self.assertEqual(filename, "src/config.py")
        self.assertIn("API_KEY", snippet)

    def test_ignores_hunk_header_lines(self) -> None:
        detail = commit_detail(files=[file_entry(patch="--- a/f\n+++ b/f\nno match here")])
        self.assertIsNone(gks.find_diff_match("API_KEY", detail))

    def test_skips_files_without_patch_text(self) -> None:
        detail = commit_detail(files=[file_entry(patch=None)])
        self.assertIsNone(gks.find_diff_match("API_KEY", detail))

    def test_returns_none_for_no_detail(self) -> None:
        self.assertIsNone(gks.find_diff_match("API_KEY", None))

    def test_reports_extra_file_count(self) -> None:
        detail = commit_detail(
            files=[
                file_entry("a.py", "+API_KEY=1"),
                file_entry("b.py", "+API_KEY=2"),
            ]
        )
        filename, _ = gks.find_diff_match("API_KEY", detail)
        self.assertIn("+1 more file(s)", filename)


class BuildRowTestCase(unittest.TestCase):
    def test_maps_fields_from_commit_summary(self) -> None:
        summary = commit_summary()
        row = gks.build_row("secret", "acme/app", "main", summary, "commit message", "snippet text")
        self.assertEqual(row.keyword, "secret")
        self.assertEqual(row.repo, "acme/app")
        self.assertEqual(row.branch, "main")
        self.assertEqual(row.commit_sha, "a" * 40)
        self.assertEqual(row.commit_message, "fix: rotate secret")
        self.assertEqual(row.committer, "Alice Example")
        self.assertEqual(row.commit_date, "2024-05-01T10:00:00Z")
        self.assertEqual(row.url, summary["html_url"])
        self.assertEqual(row.matched_in, "commit message")
        self.assertEqual(row.snippet, "snippet text")

    def test_falls_back_to_constructed_url_when_missing(self) -> None:
        summary = commit_summary()
        del summary["html_url"]
        row = gks.build_row("secret", "acme/app", "main", summary, "commit message", "s")
        self.assertEqual(row.url, f"https://github.com/acme/app/commit/{'a' * 40}")


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

    def test_gh_api_paces_every_call(self) -> None:
        self.use_gh(FakeGh([("rate_limit", ok({}))]))
        gks.gh_api("rate_limit")
        self.assertEqual(self.sleeps, [gks.GENERAL_API_SLEEP_SECONDS])

    def test_list_org_repos_paginates(self) -> None:
        page1 = [repo(f"r{i}") for i in range(100)]
        page2 = [repo("r100")]
        fake = self.use_gh(FakeGh([("orgs/acme/repos", [ok(page1), ok(page2)])]))
        repos = gks.list_org_repos("acme")
        self.assertEqual(len(repos), 101)
        self.assertEqual(len(fake.calls), 2)

    def test_list_commits_stops_at_max_commits_and_warns(self) -> None:
        page = [commit_summary(sha=f"{i:040d}") for i in range(100)]
        self.use_gh(FakeGh([("commits?", [ok(page)] * 5)]))
        warnings: list[str] = []
        commits = gks.list_commits("acme/app", "main", None, None, max_commits=50, warnings=warnings)
        self.assertEqual(len(commits), 50)
        self.assertTrue(any("MAX_COMMITS_PER_REPO=50 reached" in w for w in warnings))

    def test_list_commits_passes_since_until(self) -> None:
        fake = self.use_gh(FakeGh([("commits?", ok([]))]))
        gks.list_commits("acme/app", "main", "2024-01-01", "2024-06-01", 500, [])
        called_path = fake.calls[0]
        self.assertIn("since=2024-01-01T00%3A00%3A00Z", called_path)
        self.assertIn("until=2024-06-01T23%3A59%3A59Z", called_path)

    def test_fetch_commit_detail_warns_on_failure(self) -> None:
        self.use_gh(FakeGh([("commits/", (1, "", "HTTP 404: Not Found"))]))
        warnings: list[str] = []
        detail = gks.fetch_commit_detail("acme/app", "a" * 40, warnings)
        self.assertIsNone(detail)
        self.assertTrue(any("could not fetch commit detail" in w for w in warnings))


class ScanRepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(gks, "_sleep", lambda *_: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_gh(self, fake: FakeGh) -> FakeGh:
        patcher = mock.patch.object(gks, "_run_gh", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_matches_in_message_skip_detail_fetch_for_that_keyword(self) -> None:
        summary = commit_summary(message="rotate API_KEY now")
        fake = self.use_gh(
            FakeGh(
                [
                    ("commits?", ok([summary])),
                ]
            )
        )
        warnings: list[str] = []
        cfg = gks.Config(org="acme", keywords=("API_KEY",))
        rows, count = gks.scan_repo(repo("app"), cfg, warnings)
        self.assertEqual(count, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].matched_in, "commit message")
        # No commit-detail call needed: message already satisfied every keyword.
        self.assertFalse(any(c.startswith("repos/acme/app/commits/") for c in fake.calls))

    def test_matches_in_diff_when_not_in_message(self) -> None:
        summary = commit_summary(message="unrelated change")
        detail = commit_detail(sha=summary["sha"], files=[file_entry(patch="+API_KEY = 'x'")])
        self.use_gh(
            FakeGh(
                [
                    ("commits?", ok([summary])),
                    (f"commits/{summary['sha']}", ok(detail)),
                ]
            )
        )
        cfg = gks.Config(org="acme", keywords=("API_KEY",))
        rows, count = gks.scan_repo(repo("app"), cfg, [])
        self.assertEqual(count, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].matched_in, "src/config.py")

    def test_no_match_produces_no_row(self) -> None:
        summary = commit_summary(message="unrelated change")
        detail = commit_detail(sha=summary["sha"], files=[file_entry(patch="+ nothing interesting")])
        self.use_gh(
            FakeGh(
                [
                    ("commits?", ok([summary])),
                    (f"commits/{summary['sha']}", ok(detail)),
                ]
            )
        )
        cfg = gks.Config(org="acme", keywords=("API_KEY",))
        rows, count = gks.scan_repo(repo("app"), cfg, [])
        self.assertEqual(count, 1)
        self.assertEqual(rows, [])

    def test_skips_repo_missing_default_branch(self) -> None:
        broken = {"name": "weird", "full_name": "acme/weird", "fork": False, "default_branch": ""}
        cfg = gks.Config(org="acme", keywords=("x",))
        warnings: list[str] = []
        rows, count = gks.scan_repo(broken, cfg, warnings)
        self.assertEqual((rows, count), ([], 0))
        self.assertTrue(any("missing name or default branch" in w for w in warnings))


class RenderReportTestCase(unittest.TestCase):
    def test_escapes_untrusted_content(self) -> None:
        row = gks.MatchRow(
            keyword='<script>alert(1)</script>',
            repo="acme/app",
            url="https://github.com/acme/app/commit/deadbeef",
            branch="main",
            commit_sha="d" * 40,
            commit_message="<img src=x onerror=alert(1)>",
            committer="Mallory",
            commit_date="2024-05-01T00:00:00Z",
            matched_in="<b>src/x.py</b>",
            snippet="payload: <script>evil()</script>",
        )
        cfg = gks.Config(org="acme", keywords=("<script>",))
        report = gks.render_report(cfg, [row], [], repos_scanned=1, commits_scanned=1, generated_at="now")
        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertNotIn("<img src=x onerror=alert(1)>", report)
        self.assertNotIn("<b>src/x.py</b>", report)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", report)
        self.assertIn("acme/app", report)

    def test_includes_warnings_and_summary_counts(self) -> None:
        rows = [
            gks.MatchRow("secret", "acme/app", "u1", "main", "a" * 40, "m", "c", "d", "message", "s"),
            gks.MatchRow("secret", "acme/web", "u2", "main", "b" * 40, "m", "c", "d", "message", "s"),
        ]
        cfg = gks.Config(org="acme", keywords=("secret",))
        report = gks.render_report(cfg, rows, ["oops"], repos_scanned=2, commits_scanned=10, generated_at="now")
        self.assertIn("1 warning(s)", report)
        self.assertIn("oops", report)
        self.assertIn("2 match(es) across 2 repo(s)", report)
        self.assertIn("scanned 2 repo(s), 10 commit(s)", report)


class MainTestCase(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(gks, "_sleep", lambda *_: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_gh(self, fake: FakeGh) -> FakeGh:
        patcher = mock.patch.object(gks, "_run_gh", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def run_main(self, config: gks.Config) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = gks.main(config)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_invalid_config_exits_2_without_calling_gh(self) -> None:
        fake = self.use_gh(FakeGh())
        cfg = gks.Config(org="", keywords=())
        code, _, stderr = self.run_main(cfg)
        self.assertEqual(code, 2)
        self.assertIn("config error", stderr)
        self.assertEqual(fake.calls, [])

    def test_end_to_end_writes_report(self) -> None:
        summary = commit_summary(message="contains SECRET right here")
        self.use_gh(
            FakeGh(
                [
                    ("rate_limit", ok({})),
                    ("orgs/acme/repos", ok([repo("app")])),
                    ("commits?", ok([summary])),
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            cfg = gks.Config(org="acme", keywords=("SECRET",), out_dir=out_dir)
            code, stdout, stderr = self.run_main(cfg)
            self.assertEqual(code, 0, stderr)
            report_path = out_dir / cfg.out_file
            self.assertTrue(report_path.exists())
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("acme/app", content)
            self.assertIn("SECRET", content)
            self.assertIn("found 1 match(es) across 1 repo(s)", stdout)

    def test_strict_exits_2_on_warnings(self) -> None:
        self.use_gh(
            FakeGh(
                [
                    ("rate_limit", ok({})),
                    ("orgs/acme/repos", ok([])),  # no repos -> "no repos matched" warning
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = gks.Config(org="acme", keywords=("secret",), out_dir=Path(tmp), strict=True)
            code, _, stderr = self.run_main(cfg)
            self.assertEqual(code, 2)
            self.assertIn("no repos matched", stderr)

    def test_non_strict_returns_0_on_warnings(self) -> None:
        self.use_gh(
            FakeGh(
                [
                    ("rate_limit", ok({})),
                    ("orgs/acme/repos", ok([])),
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = gks.Config(org="acme", keywords=("secret",), out_dir=Path(tmp), strict=False)
            code, _, _ = self.run_main(cfg)
            self.assertEqual(code, 0)

    def test_gh_missing_returns_1(self) -> None:
        def missing(argv: list[str]) -> tuple[int, str, str]:
            raise gks.GhError("gh not found")

        self.use_gh(missing)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = gks.Config(org="acme", keywords=("secret",), out_dir=Path(tmp))
            code, _, stderr = self.run_main(cfg)
            self.assertEqual(code, 1)
            self.assertIn("gh not found", stderr)


if __name__ == "__main__":
    unittest.main()
