"""Offline tests for github_activity_scanner.

Every test replaces the gh subprocess seam (``scanner._run_gh``) with a
scripted fake and the sleep hook (``scanner._sleep``) with a recorder, so no
test touches the network, the gh binary, or real time.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
import urllib.parse
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import github_activity_scanner as scanner


def ok(payload) -> tuple[int, str, str]:
    return (0, json.dumps(payload), "")


def commit_item(
    repo: str = "acme/app",
    sha: str = "a" * 40,
    day: str = "2024-05-01",
    message: str = "fix: thing\n\nlong body",
    login: str | None = "alice",
) -> dict:
    stamp = f"{day}T10:00:00Z"
    return {
        "sha": sha,
        "html_url": f"https://github.com/{repo}/commit/{sha}",
        "repository": {"full_name": repo},
        "commit": {
            "message": message,
            "author": {"date": stamp},
            "committer": {"date": stamp},
        },
        "author": {"login": login} if login else None,
    }


def pr_item(
    repo: str = "acme/app",
    number: int = 7,
    title: str = "Add feature",
    author: str = "bob",
    created: str = "2024-02-01T00:00:00Z",
) -> dict:
    return {
        "number": number,
        "title": title,
        "user": {"login": author},
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "created_at": created,
    }


def review(login: str, state: str, submitted: str) -> dict:
    return {"user": {"login": login}, "state": state, "submitted_at": submitted}


class FakeGh:
    """Scripted stand-in for scanner._run_gh.

    ``routes`` is an ordered list of (substring, response) pairs matched
    against the request path; a response given as a list serves successive
    calls (the last entry repeats). Unmatched search paths return an empty
    result set and anything else returns an empty object.
    """

    def __init__(self, routes: list[tuple[str, object]] | None = None) -> None:
        self.routes = [[key, value] for key, value in (routes or [])]
        self.calls: list[str] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        assert argv[0] == "api"
        path = argv[1]
        self.calls.append(path)
        for route in self.routes:
            key, value = route
            if key in path:
                if isinstance(value, list):
                    return value.pop(0) if len(value) > 1 else value[0]
                return value
        if path.startswith("search/"):
            return ok({"total_count": 0, "items": []})
        return ok({})


class ScannerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        patcher = mock.patch.object(scanner, "_sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_gh(self, fake) -> FakeGh:
        patcher = mock.patch.object(scanner, "_run_gh", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = scanner.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()


class GhSeamTests(ScannerTestCase):
    def test_missing_gh_binary_maps_to_friendly_error(self) -> None:
        with mock.patch.object(scanner.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(scanner.GhError) as ctx:
                scanner._run_gh(["api", "rate_limit"])
        self.assertIn("PATH", str(ctx.exception))
        self.assertIn("gh auth login", str(ctx.exception))

    def test_http_401_mentions_gh_auth_login(self) -> None:
        self.use_gh(FakeGh([("rate_limit", (1, "", "gh: Bad credentials (HTTP 401)"))]))
        with self.assertRaises(scanner.GhError) as ctx:
            scanner.gh_api("rate_limit")
        self.assertIn("gh auth login", str(ctx.exception))

    def test_rate_limited_call_retries_then_succeeds(self) -> None:
        fake = self.use_gh(
            FakeGh(
                [
                    (
                        "rate_limit",
                        [(1, "", "gh: API rate limit exceeded (HTTP 403)"), ok({"resources": {}})],
                    )
                ]
            )
        )
        self.assertEqual(scanner.gh_api("rate_limit"), {"resources": {}})
        self.assertEqual(len(fake.calls), 2)
        self.assertIn(scanner.RATE_LIMIT_SLEEP_SECONDS, self.sleeps)

    def test_persistent_rate_limit_gives_up_after_retries(self) -> None:
        fake = self.use_gh(FakeGh([("rate_limit", (1, "", "HTTP 429 too many requests"))]))
        with self.assertRaises(scanner.GhError):
            scanner.gh_api("rate_limit")
        self.assertEqual(len(fake.calls), 1 + scanner.MAX_RATE_LIMIT_RETRIES)
        self.assertEqual(
            self.sleeps,
            [scanner.RATE_LIMIT_SLEEP_SECONDS] * scanner.MAX_RATE_LIMIT_RETRIES,
        )

    def test_search_paths_are_throttled_and_rest_paths_are_not(self) -> None:
        self.use_gh(FakeGh())
        scanner.gh_api("search/users?q=x")
        scanner.gh_api("repos/acme/app/commits/abc")
        self.assertEqual(self.sleeps, [scanner.SEARCH_SLEEP_SECONDS])

    def test_undecodable_json_raises_gh_error(self) -> None:
        self.use_gh(FakeGh([("rate_limit", (0, "<html>oops</html>", ""))]))
        with self.assertRaises(scanner.GhError) as ctx:
            scanner.gh_api("rate_limit")
        self.assertIn("undecodable", str(ctx.exception))


class SearchAllTests(ScannerTestCase):
    def test_collects_multiple_pages(self) -> None:
        page1 = ok({"total_count": 150, "items": [{"d": "2020-01-01"} for _ in range(100)]})
        page2 = ok({"total_count": 150, "items": [{"d": "2020-06-01"} for _ in range(50)]})
        fake = self.use_gh(FakeGh([("&page=1", page1), ("&page=2", page2)]))
        warnings: list[str] = []
        items = scanner.search_all(
            "search/commits",
            "author-email:x@y.z",
            "author-date",
            lambda item: item.get("d", ""),
            "2008-01-01",
            "2026-01-01",
            "test",
            warnings,
        )
        self.assertEqual(len(items), 150)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(warnings, [])

    def test_slices_windows_when_total_exceeds_cap(self) -> None:
        # Shrink the page geometry so the cap is reachable with tiny payloads.
        for name, value in (
            ("SEARCH_PAGE_SIZE", 2),
            ("MAX_SEARCH_RESULTS", 4),
            ("MAX_SEARCH_PAGES", 2),
        ):
            patcher = mock.patch.object(scanner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        window1 = {
            1: ok({"total_count": 6, "items": [{"d": "2024-01-01"}, {"d": "2024-02-01"}]}),
            2: ok({"total_count": 6, "items": [{"d": "2024-05-01"}, {"d": "2024-06-01"}]}),
        }
        window2 = {
            1: ok({"total_count": 3, "items": [{"d": "2024-06-01"}, {"d": "2024-07-01"}]}),
            2: ok({"total_count": 3, "items": [{"d": "2024-08-01"}]}),
        }

        def fake(argv: list[str]) -> tuple[int, str, str]:
            path = argv[1]
            calls.append(path)
            query = urllib.parse.unquote(path.split("q=")[1].split("&")[0])
            page = int(path.split("&page=")[1])
            window = window1 if "2008-01-01.." in query else window2
            return window[page]

        calls: list[str] = []
        patcher = mock.patch.object(scanner, "_run_gh", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

        warnings: list[str] = []
        items = scanner.search_all(
            "search/commits",
            "author-email:x@y.z",
            "author-date",
            lambda item: item.get("d", ""),
            "2008-01-01",
            "2026-01-01",
            "test",
            warnings,
        )
        # 4 items from window one, 3 from window two; the boundary-day overlap
        # ({"d": "2024-06-01"}) is the caller's dedupe job.
        self.assertEqual(len(items), 7)
        self.assertTrue(any("author-date:2024-06-01.." in urllib.parse.unquote(c) for c in calls))
        self.assertEqual(warnings, [])

    def test_single_day_over_cap_truncates_with_warning(self) -> None:
        for name, value in (
            ("SEARCH_PAGE_SIZE", 2),
            ("MAX_SEARCH_RESULTS", 4),
            ("MAX_SEARCH_PAGES", 2),
        ):
            patcher = mock.patch.object(scanner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        burst = ok({"total_count": 9, "items": [{"d": "2008-01-01"}, {"d": "2008-01-01"}]})
        empty = ok({"total_count": 0, "items": []})
        fake = self.use_gh(FakeGh([("2008-01-01..", burst), ("2008-01-02..", empty)]))
        warnings: list[str] = []
        items = scanner.search_all(
            "search/commits",
            "author-email:x@y.z",
            "author-date",
            lambda item: item.get("d", ""),
            "2008-01-01",
            "2026-01-01",
            "test",
            warnings,
        )
        self.assertEqual(len(items), 4)
        self.assertEqual(len(warnings), 1)
        self.assertIn("truncated", warnings[0])
        self.assertTrue(any("2008-01-02.." in urllib.parse.unquote(c) for c in fake.calls))


class ScanCommitsTests(ScannerTestCase):
    def test_unscoped_query_shape(self) -> None:
        fake = self.use_gh(FakeGh())
        warnings: list[str] = []
        rows, votes = scanner.scan_commits(
            "dev@x.com", [""], "2008-01-01", "2026-01-01", warnings
        )
        self.assertEqual(rows, [])
        self.assertEqual(votes, {})
        self.assertEqual(len(fake.calls), 1)
        query = urllib.parse.unquote(fake.calls[0].split("q=")[1].split("&")[0])
        self.assertEqual(query, "author-email:dev@x.com author-date:2008-01-01..2026-01-01")

    def test_scoped_queries_dedupe_and_vote(self) -> None:
        shared = commit_item(sha="c" * 40, day="2024-03-01")
        org_only = commit_item(sha="d" * 40, day="2024-04-01", login="alice")
        repo_only = commit_item(sha="e" * 40, day="2024-05-01", login=None)
        fake = self.use_gh(
            FakeGh(
                [
                    ("org%3Aacme", ok({"total_count": 2, "items": [shared, org_only]})),
                    (
                        "repo%3Aacme%2Fapp",
                        ok({"total_count": 2, "items": [shared, repo_only]}),
                    ),
                ]
            )
        )
        warnings: list[str] = []
        rows, votes = scanner.scan_commits(
            "dev@x.com", ["org:acme", "repo:acme/app"], "2008-01-01", "2026-01-01", warnings
        )
        self.assertEqual(len(rows), 3)  # shared commit counted once
        self.assertEqual(votes["alice"], 2)  # null author tolerated, not voted
        self.assertEqual(rows[0].message, "fix: thing")  # first line only
        self.assertEqual(rows[0].additions, "")  # stats not fetched here
        self.assertEqual(len(fake.calls), 2)


class ResolveLoginTests(ScannerTestCase):
    def test_majority_vote_needs_no_api_call(self) -> None:
        fake = self.use_gh(FakeGh())
        login = scanner.resolve_login("dev@x.com", Counter({"alice": 3, "bot": 1}), [])
        self.assertEqual(login, "alice")
        self.assertEqual(fake.calls, [])

    def test_fallback_user_search_single_match(self) -> None:
        fake = self.use_gh(
            FakeGh([("search/users", ok({"total_count": 1, "items": [{"login": "solo"}]}))])
        )
        warnings: list[str] = []
        self.assertEqual(scanner.resolve_login("dev@x.com", Counter(), warnings), "solo")
        self.assertEqual(warnings, [])
        self.assertIn("in%3Aemail", fake.calls[0])

    def test_ambiguous_user_search_stays_unresolved(self) -> None:
        self.use_gh(
            FakeGh(
                [
                    (
                        "search/users",
                        ok({"total_count": 2, "items": [{"login": "a"}, {"login": "b"}]}),
                    )
                ]
            )
        )
        warnings: list[str] = []
        self.assertEqual(scanner.resolve_login("dev@x.com", Counter(), warnings), "")
        self.assertEqual(len(warnings), 1)
        self.assertIn("reviews skipped", warnings[0])


class FetchStatsTests(ScannerTestCase):
    def test_fills_stats_and_survives_one_failure(self) -> None:
        newest = commit_item(sha="f" * 40, day="2024-06-01")
        oldest = commit_item(sha="9" * 40, day="2023-01-01")
        detail = ok({"stats": {"additions": 10, "deletions": 4, "total": 14}, "files": [{}, {}, {}]})
        self.use_gh(
            FakeGh(
                [
                    ("commits/" + "f" * 40, detail),
                    ("commits/" + "9" * 40, (1, "", "gh: boom (HTTP 500)")),
                ]
            )
        )
        warnings: list[str] = []
        rows = [
            scanner.CommitRow(
                email="dev@x.com",
                login="",
                repo="acme/app",
                sha=item["sha"],
                author_date=item["commit"]["author"]["date"],
                committer_date=item["commit"]["committer"]["date"],
                message="m",
                additions="",
                deletions="",
                total_changes="",
                files_changed="",
                html_url="",
            )
            for item in (newest, oldest)
        ]
        fetched = scanner.fetch_commit_stats("dev@x.com", rows, 300, warnings)
        self.assertEqual(fetched, 1)
        self.assertEqual(rows[0].additions, "10")
        self.assertEqual(rows[0].deletions, "4")
        self.assertEqual(rows[0].total_changes, "14")
        self.assertEqual(rows[0].files_changed, "3")
        self.assertEqual(rows[1].additions, "")  # failed fetch leaves blanks
        self.assertEqual(len(warnings), 1)
        self.assertIn("stats fetch failed", warnings[0])

    def test_cap_fetches_newest_first(self) -> None:
        fake = self.use_gh(
            FakeGh(
                [
                    (
                        "commits/",
                        ok({"stats": {"additions": 1, "deletions": 1, "total": 2}, "files": [{}]}),
                    )
                ]
            )
        )
        rows = [
            scanner.CommitRow(
                email="dev@x.com",
                login="",
                repo="acme/app",
                sha=sha,
                author_date=f"{day}T00:00:00Z",
                committer_date=f"{day}T00:00:00Z",
                message="m",
                additions="",
                deletions="",
                total_changes="",
                files_changed="",
                html_url="",
            )
            for sha, day in (("1" * 40, "2020-01-01"), ("2" * 40, "2024-01-01"))
        ]
        warnings: list[str] = []
        fetched = scanner.fetch_commit_stats("dev@x.com", rows, 1, warnings)
        self.assertEqual(fetched, 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("2" * 40, fake.calls[0])  # newest commit wins the budget
        self.assertEqual(len(warnings), 1)
        self.assertIn("--max-commits=1", warnings[0])


class ScanReviewsTests(ScannerTestCase):
    def test_query_shape_filtering_and_dates(self) -> None:
        item = pr_item()
        reviews_payload = ok(
            [
                review("alice", "APPROVED", "2024-03-01T12:00:00Z"),
                review("Alice", "CHANGES_REQUESTED", "2024-02-15T12:00:00Z"),
                review("bob", "APPROVED", "2024-03-02T12:00:00Z"),
                review("alice", "COMMENTED", "2020-01-01T12:00:00Z"),
                {"user": {"login": "alice"}, "state": "PENDING", "submitted_at": None},
            ]
        )
        fake = self.use_gh(
            FakeGh(
                [
                    ("search/issues", ok({"total_count": 1, "items": [item]})),
                    ("pulls/7/reviews", reviews_payload),
                ]
            )
        )
        warnings: list[str] = []
        rows = scanner.scan_reviews(
            "dev@x.com", "alice", [""], "2024-01-01", "2026-01-01", 200, warnings
        )
        search_call = next(c for c in fake.calls if c.startswith("search/issues"))
        query = urllib.parse.unquote(search_call.split("q=")[1].split("&")[0])
        self.assertIn("is:pull-request", query)
        self.assertIn("reviewed-by:alice", query)
        self.assertIn("updated:>=2024-01-01", query)
        self.assertIn("created:2008-01-01..2026-01-01", query)
        self.assertIn("advanced_search=true", search_call)

        # bob's review, alice's out-of-window review, and alice's unsubmitted
        # PENDING draft are dropped; the mixed-case login still matches.
        self.assertEqual(len(rows), 2)
        states = {row.review_state for row in rows}
        self.assertEqual(states, {"APPROVED", "CHANGES_REQUESTED"})
        self.assertTrue(all(row.repo == "acme/app" for row in rows))
        self.assertTrue(all(row.pr_number == "7" for row in rows))
        self.assertEqual(rows[0].pr_author, "bob")
        self.assertEqual(warnings, [])

    def test_max_prs_keeps_newest_and_warns(self) -> None:
        old_pr = pr_item(number=1, created="2020-01-01T00:00:00Z")
        new_pr = pr_item(number=2, created="2025-01-01T00:00:00Z")
        fake = self.use_gh(
            FakeGh(
                [
                    ("search/issues", ok({"total_count": 2, "items": [old_pr, new_pr]})),
                    ("pulls/2/reviews", ok([review("alice", "APPROVED", "2025-02-01T00:00:00Z")])),
                    ("pulls/1/reviews", ok([review("alice", "APPROVED", "2020-02-01T00:00:00Z")])),
                ]
            )
        )
        warnings: list[str] = []
        rows = scanner.scan_reviews(
            "dev@x.com", "alice", [""], "2008-01-01", "2026-01-01", 1, warnings
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pr_number, "2")
        self.assertFalse(any("pulls/1/reviews" in c for c in fake.calls))
        self.assertEqual(len(warnings), 1)
        self.assertIn("--max-prs", warnings[0])


class MainTests(ScannerTestCase):
    def full_fake(self) -> FakeGh:
        sha = "a" * 40
        return FakeGh(
            [
                ("rate_limit", ok({})),
                ("search/commits", ok({"total_count": 1, "items": [commit_item(sha=sha)]})),
                (
                    "commits/" + sha,
                    ok({"stats": {"additions": 5, "deletions": 2, "total": 7}, "files": [{}, {}]}),
                ),
                ("search/issues", ok({"total_count": 1, "items": [pr_item()]})),
                ("pulls/7/reviews", ok([review("alice", "APPROVED", "2024-03-01T12:00:00Z")])),
            ]
        )

    def test_end_to_end_writes_both_csvs(self) -> None:
        self.use_gh(self.full_fake())
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self.run_main(
                ["dev@x.com", "--out-dir", tmp, "--until", "2026-01-01"]
            )
            self.assertEqual(code, 0, stderr)
            with open(Path(tmp) / "commits.csv", encoding="utf-8", newline="") as handle:
                commits = list(csv.reader(handle))
            with open(Path(tmp) / "reviews.csv", encoding="utf-8", newline="") as handle:
                reviews_csv = list(csv.reader(handle))
        self.assertEqual(tuple(commits[0]), scanner.COMMIT_FIELDS)
        self.assertEqual(tuple(reviews_csv[0]), scanner.REVIEW_FIELDS)
        self.assertEqual(len(commits), 2)
        self.assertEqual(len(reviews_csv), 2)
        commit = dict(zip(commits[0], commits[1]))
        self.assertEqual(commit["message"], "fix: thing")
        self.assertEqual(commit["login"], "alice")
        self.assertEqual(commit["additions"], "5")
        self.assertEqual(commit["files_changed"], "2")
        review_row = dict(zip(reviews_csv[0], reviews_csv[1]))
        self.assertEqual(review_row["review_state"], "APPROVED")
        self.assertIn("dev@x.com -> login: alice", stdout)
        self.assertIn("wrote 1 commit rows", stdout)

    def test_no_stats_leaves_blank_columns_and_skips_detail_calls(self) -> None:
        fake = self.use_gh(self.full_fake())
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_main(
                ["dev@x.com", "--out-dir", tmp, "--until", "2026-01-01", "--no-stats"]
            )
            self.assertEqual(code, 0, stderr)
            with open(Path(tmp) / "commits.csv", encoding="utf-8", newline="") as handle:
                commits = list(csv.reader(handle))
        commit = dict(zip(commits[0], commits[1]))
        self.assertEqual(commit["additions"], "")  # blank, not 0
        self.assertFalse(any("/commits/" in call for call in fake.calls))

    def test_unresolved_login_with_strict_exits_2(self) -> None:
        self.use_gh(
            FakeGh(
                [
                    ("rate_limit", ok({})),
                    ("search/commits", ok({"total_count": 0, "items": []})),
                    ("search/users", ok({"total_count": 0, "items": []})),
                ]
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self.run_main(
                ["ghost@x.com", "--out-dir", tmp, "--strict", "--until", "2026-01-01"]
            )
            self.assertEqual(code, 2)
            self.assertIn("reviews skipped", stderr)
            # Empty scans still produce stable-schema CSVs.
            header = (Path(tmp) / "commits.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(tuple(header.split(",")), scanner.COMMIT_FIELDS)
        self.assertIn("commits: 0", stdout)

    def test_gh_failure_exits_1(self) -> None:
        self.use_gh(FakeGh([("rate_limit", (1, "", "gh: Bad credentials (HTTP 401)"))]))
        code, _, stderr = self.run_main(["dev@x.com"])
        self.assertEqual(code, 1)
        self.assertIn("gh auth login", stderr)

    def test_emails_file_merges_and_dedupes(self) -> None:
        fake = self.use_gh(FakeGh([("rate_limit", ok({}))]))
        with tempfile.TemporaryDirectory() as tmp:
            emails_file = Path(tmp) / "emails.txt"
            emails_file.write_text(
                "a@x.com\n# comment\n\nb@x.com\na@x.com\n", encoding="utf-8"
            )
            code, _, _ = self.run_main(
                [
                    "a@x.com",
                    "--emails-file",
                    str(emails_file),
                    "--out-dir",
                    tmp,
                    "--until",
                    "2026-01-01",
                ]
            )
        self.assertEqual(code, 0)
        commit_searches = [c for c in fake.calls if c.startswith("search/commits")]
        self.assertEqual(len(commit_searches), 2)  # one per unique email
        self.assertTrue(any("a%40x.com" in c for c in commit_searches))
        self.assertTrue(any("b%40x.com" in c for c in commit_searches))

    def test_argument_validation_errors(self) -> None:
        # A fake seam even here: if a validation check ever regresses, the
        # test must fail offline instead of invoking the real gh binary.
        self.use_gh(FakeGh())
        for argv in (
            [],  # no emails at all
            ["a@x.com", "--since", "2025-01-01", "--until", "2024-01-01"],
            ["a@x.com", "--repo", "not-owner-slash-name"],
            ["a@x.com", "--since", "not-a-date"],
        ):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        scanner.main(argv)
                self.assertEqual(ctx.exception.code, 2)


class UtcDayTests(unittest.TestCase):
    def test_timestamps_normalize_to_the_utc_day(self) -> None:
        # search/commits mixes Z-normalized and local-offset renderings.
        self.assertEqual(scanner._utc_day("2024-06-10T03:00:00+09:00"), "2024-06-09")
        self.assertEqual(scanner._utc_day("2024-03-01T23:59:59.000Z"), "2024-03-01")
        self.assertEqual(scanner._utc_day("2024-03-01T15:59:59.000-08:00"), "2024-03-01")
        self.assertEqual(scanner._utc_day("2024-06-01"), "2024-06-01")
        self.assertEqual(scanner._utc_day(""), "")
        self.assertEqual(scanner._utc_day("not-a-date"), "not-a-date")


class RegressionTests(ScannerTestCase):
    def _shrink_search_geometry(self) -> None:
        for name, value in (
            ("SEARCH_PAGE_SIZE", 2),
            ("MAX_SEARCH_RESULTS", 4),
            ("MAX_SEARCH_PAGES", 2),
        ):
            patcher = mock.patch.object(scanner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_permission_403_fails_fast_without_retry(self) -> None:
        fake = self.use_gh(
            FakeGh(
                [
                    (
                        "rate_limit",
                        (1, "", "gh: Resource protected by organization SAML enforcement (HTTP 403)"),
                    )
                ]
            )
        )
        with self.assertRaises(scanner.GhError):
            scanner.gh_api("rate_limit")
        self.assertEqual(len(fake.calls), 1)  # no backoff retries burned
        self.assertEqual(self.sleeps, [])

    def test_incomplete_results_emit_a_warning_once(self) -> None:
        payload = ok(
            {"total_count": 1, "incomplete_results": True, "items": [{"d": "2020-01-01"}]}
        )
        self.use_gh(FakeGh([("search/commits", payload)]))
        warnings: list[str] = []
        items = scanner.search_all(
            "search/commits",
            "author-email:x@y.z",
            "author-date",
            lambda item: item.get("d", ""),
            "2008-01-01",
            "2026-01-01",
            "test",
            warnings,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("incomplete", warnings[0])

    def test_over_cap_window_with_empty_page_returns_cleanly(self) -> None:
        # total_count is an approximation: over-the-cap totals can arrive
        # with an empty items array, which must not crash the cursor logic.
        self._shrink_search_geometry()
        self.use_gh(FakeGh([("search/commits", ok({"total_count": 9, "items": []}))]))
        items = scanner.search_all(
            "search/commits",
            "author-email:x@y.z",
            "author-date",
            lambda item: item.get("d", ""),
            "2008-01-01",
            "2026-01-01",
            "test",
            [],
        )
        self.assertEqual(items, [])

    def test_window_cursor_uses_utc_day_of_offset_timestamps(self) -> None:
        self._shrink_search_geometry()
        # Window one's last item is 03:00 on June 10 in UTC+9 — still June 9
        # in UTC, so the next window must start at 2024-06-09, not 06-10.
        window1 = ok(
            {
                "total_count": 6,
                "items": [{"d": "2024-06-09T01:00:00Z"}, {"d": "2024-06-10T03:00:00+09:00"}],
            }
        )
        window2 = ok({"total_count": 1, "items": [{"d": "2024-06-09T20:00:00Z"}]})
        fake = self.use_gh(
            FakeGh(
                [
                    ("2008-01-01..", [window1, window1]),
                    ("2024-06-09..", window2),
                ]
            )
        )
        items = scanner.search_all(
            "search/commits",
            "author-email:x@y.z",
            "author-date",
            lambda item: item.get("d", ""),
            "2008-01-01",
            "2026-01-01",
            "test",
            [],
        )
        self.assertEqual(len(items), 5)
        decoded = [urllib.parse.unquote(call) for call in fake.calls]
        self.assertTrue(any("author-date:2024-06-09..2026-01-01" in call for call in decoded))
        self.assertFalse(any("author-date:2024-06-10..2026-01-01" in call for call in decoded))

    def test_pr_reviews_paginate_past_a_full_page(self) -> None:
        full_page = ok(
            [review("alice", "APPROVED", "2024-03-01T12:00:00Z") for _ in range(100)]
        )
        short_page = ok([review("alice", "COMMENTED", "2024-03-02T12:00:00Z")])
        fake = self.use_gh(
            FakeGh(
                [
                    ("search/issues", ok({"total_count": 1, "items": [pr_item()]})),
                    ("pulls/7/reviews?per_page=100&page=1", full_page),
                    ("pulls/7/reviews?per_page=100&page=2", short_page),
                ]
            )
        )
        rows = scanner.scan_reviews(
            "dev@x.com", "alice", [""], "2008-01-01", "2026-01-01", 200, []
        )
        self.assertEqual(len(rows), 101)
        review_calls = [c for c in fake.calls if "pulls/7/reviews" in c]
        self.assertEqual(len(review_calls), 2)


if __name__ == "__main__":
    unittest.main()
