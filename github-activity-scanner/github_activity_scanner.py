#!/usr/bin/env python3
"""Scan GitHub for commit and pull-request-review activity by author email.

Given one or more email addresses, the script reports every indexed commit
authored under those emails (repo, dates, first message line, magnitude of
changes) and every pull-request review left by the matching GitHub account
(state and timestamp, so approvals are a filter on ``review_state ==
APPROVED``). Results are written to ``commits.csv`` and ``reviews.csv``.

The script is read-only and dependency-free: all GitHub access goes through
the GitHub CLI (``gh api``), so authentication comes from ``gh auth login``
and no token ever touches this file. Without ``--org``/``--repo`` it searches
all of public GitHub, subject to the search-index caveats in the README.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


SEARCH_PAGE_SIZE = 100
MAX_SEARCH_RESULTS = 1000  # GitHub returns at most 1000 results per search query
MAX_SEARCH_PAGES = 10
SEARCH_SLEEP_SECONDS = 2.1  # stays under the 30 searches/minute limit
RATE_LIMIT_SLEEP_SECONDS = 60.0
MAX_RATE_LIMIT_RETRIES = 2
DEFAULT_SINCE = "2008-01-01"  # GitHub launched in 2008; no commit is indexed earlier
DEFAULT_MAX_COMMITS = 300
DEFAULT_MAX_PRS = 200
MAX_PRINTED_WARNINGS = 10


class GhError(RuntimeError):
    """A gh invocation failed in a way the scan cannot recover from."""


@dataclass
class CommitRow:
    email: str
    login: str
    repo: str
    sha: str
    author_date: str
    committer_date: str
    message: str
    additions: str
    deletions: str
    total_changes: str
    files_changed: str
    html_url: str


@dataclass
class ReviewRow:
    email: str
    login: str
    repo: str
    pr_number: str
    pr_title: str
    pr_author: str
    review_state: str
    submitted_at: str
    pr_url: str


COMMIT_FIELDS = tuple(field.name for field in fields(CommitRow))
REVIEW_FIELDS = tuple(field.name for field in fields(ReviewRow))

_sleep = time.sleep


def _parse_utc(timestamp: str) -> datetime | None:
    """Parse an ISO timestamp (Z or offset form) into an aware UTC datetime."""
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_day(timestamp: str) -> str:
    """The UTC calendar day of a timestamp.

    Commit search renders author dates in the author's local offset (mixed
    with Z-normalized forms), while the ``author-date:`` qualifier compares
    UTC instants — the date cursor must slice on the UTC day or commits near
    window boundaries are silently skipped.
    """
    parsed = _parse_utc(timestamp)
    return parsed.date().isoformat() if parsed else (timestamp or "")[:10]


def _run_gh(argv: list[str]) -> tuple[int, str, str]:
    """Run ``gh`` with the given arguments; the only subprocess call in the file."""
    try:
        proc = subprocess.run(
            ["gh", *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise GhError(
            "GitHub CLI (gh) was not found on PATH; install it from "
            "https://cli.github.com/ and run 'gh auth login'."
        ) from exc
    return proc.returncode, proc.stdout, proc.stderr


def gh_api(path: str) -> Any:
    """Call ``gh api PATH`` and return the parsed JSON response.

    Search endpoints are throttled below GitHub's 30 requests/minute search
    limit. 403/429 responses get a fixed sleep-and-retry; 401 becomes a clear
    "run gh auth login" error.
    """
    if path.startswith("search/"):
        _sleep(SEARCH_SLEEP_SECONDS)
    retries = 0
    while True:
        code, stdout, stderr = _run_gh(["api", path])
        if code == 0:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise GhError(
                    f"gh api {path} returned undecodable JSON: {stdout[:200]!r}"
                ) from exc
        if "HTTP 401" in stderr:
            raise GhError("gh is not authenticated; run 'gh auth login' and retry.")
        # A 403 is only worth a backoff when it is actually about rate limits;
        # SAML/permission 403s never recover by waiting.
        rate_limited = "HTTP 429" in stderr or (
            "HTTP 403" in stderr and "rate limit" in stderr.lower()
        )
        if rate_limited and retries < MAX_RATE_LIMIT_RETRIES:
            retries += 1
            _sleep(RATE_LIMIT_SLEEP_SECONDS)
            continue
        snippet = " ".join(stderr.split())[:200] or " ".join(stdout.split())[:200]
        raise GhError(f"gh api {path} failed (exit {code}): {snippet}")


def search_all(
    endpoint: str,
    base_query: str,
    date_field: str,
    item_day: Callable[[dict], str],
    since: str,
    until: str,
    label: str,
    warnings: list[str],
) -> list[dict]:
    """Collect all results for a search query, working around the 1000-result cap.

    Queries ascending by ``date_field`` (one name for both the range qualifier
    and the sort, since the date cursor is only correct when they agree);
    whenever a window reports more than 1000 total results, the window start
    advances to the UTC day of the last item received and the query reruns.
    The boundary day overlaps between windows, so callers must dedupe. A
    single day holding more than 1000 results cannot be sliced further and is
    truncated with a warning.
    """
    items: list[dict] = []
    window_start = since
    reported_incomplete = False
    while True:
        query = f"{base_query} {date_field}:{window_start}..{until}"
        encoded = urllib.parse.quote(query, safe="")
        total = 0
        page_items: list[dict] = []
        for page in range(1, MAX_SEARCH_PAGES + 1):
            path = (
                f"{endpoint}?q={encoded}&sort={date_field}&order=asc"
                f"&per_page={SEARCH_PAGE_SIZE}&page={page}"
            )
            if endpoint == "search/issues":
                path += "&advanced_search=true"
            payload = gh_api(path)
            total = int(payload.get("total_count") or 0)
            batch = payload.get("items") or []
            page_items.extend(batch)
            if payload.get("incomplete_results") and not reported_incomplete:
                reported_incomplete = True
                warnings.append(
                    f"{label}: GitHub reported incomplete results "
                    f"(search timed out); counts may be low"
                )
            if len(batch) < SEARCH_PAGE_SIZE or len(page_items) >= min(total, MAX_SEARCH_RESULTS):
                break
        items.extend(page_items)
        if total <= MAX_SEARCH_RESULTS or not page_items:
            return items
        last_day = _utc_day(item_day(page_items[-1]))
        if last_day > window_start:
            window_start = last_day
            continue
        warnings.append(
            f"{label}: more than {MAX_SEARCH_RESULTS} results dated {window_start}; "
            "that day is truncated"
        )
        next_day = (date.fromisoformat(window_start) + timedelta(days=1)).isoformat()
        if next_day > until:
            return items
        window_start = next_day


def build_scopes(orgs: Sequence[str], repos: Sequence[str]) -> list[str]:
    """One scope qualifier per query: advanced search ANDs repeated qualifiers."""
    scopes = [f"org:{org}" for org in orgs] + [f"repo:{repo}" for repo in repos]
    return scopes or [""]


def scan_commits(
    email: str,
    scopes: Sequence[str],
    since: str,
    until: str,
    warnings: list[str],
) -> tuple[list[CommitRow], Counter]:
    """Find commits authored under ``email``; also tally account-login votes."""
    rows: list[CommitRow] = []
    votes: Counter = Counter()
    seen: set[tuple[str, str]] = set()
    for scope in scopes:
        base_query = f"author-email:{email}"
        if scope:
            base_query += f" {scope}"
        items = search_all(
            "search/commits",
            base_query,
            "author-date",
            lambda item: ((item.get("commit") or {}).get("author") or {}).get("date", ""),
            since,
            until,
            f"commit search for {email}",
            warnings,
        )
        for item in items:
            repo = ((item.get("repository") or {}).get("full_name")) or ""
            sha = item.get("sha") or ""
            if (repo, sha) in seen:
                continue
            seen.add((repo, sha))
            commit = item.get("commit") or {}
            account = item.get("author") or {}
            if account.get("login"):
                votes[account["login"]] += 1
            message_lines = (commit.get("message") or "").splitlines()
            rows.append(
                CommitRow(
                    email=email,
                    login="",
                    repo=repo,
                    sha=sha,
                    author_date=((commit.get("author") or {}).get("date")) or "",
                    committer_date=((commit.get("committer") or {}).get("date")) or "",
                    message=message_lines[0] if message_lines else "",
                    additions="",
                    deletions="",
                    total_changes="",
                    files_changed="",
                    html_url=item.get("html_url") or "",
                )
            )
    return rows, votes


def resolve_login(email: str, votes: Counter, warnings: list[str]) -> str:
    """Map an email to a GitHub login, or "" when no unique account matches."""
    if votes:
        return votes.most_common(1)[0][0]
    query = urllib.parse.quote(f"{email} in:email", safe="")
    payload = gh_api(f"search/users?q={query}&per_page=5")
    items = payload.get("items") or []
    if int(payload.get("total_count") or 0) == 1 and items:
        return items[0].get("login") or ""
    warnings.append(f"{email}: could not resolve a unique GitHub login; reviews skipped")
    return ""


def fetch_commit_stats(
    email: str,
    rows: Sequence[CommitRow],
    max_commits: int,
    warnings: list[str],
) -> int:
    """Fill additions/deletions/files for the newest ``max_commits`` commits."""
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    ordered = sorted(
        rows, key=lambda row: _parse_utc(row.author_date) or epoch, reverse=True
    )
    fetched = 0
    for index, row in enumerate(ordered):
        if index >= max_commits:
            warnings.append(
                f"{email}: per-commit stats capped at --max-commits={max_commits}; "
                f"{len(ordered) - index} older commits left without stats"
            )
            break
        if not row.repo or not row.sha:
            continue
        try:
            detail = gh_api(f"repos/{row.repo}/commits/{row.sha}")
        except GhError as exc:
            warnings.append(f"{row.repo}@{row.sha[:7]}: stats fetch failed: {exc}")
            continue
        stats = detail.get("stats") or {}
        row.additions = str(stats.get("additions", ""))
        row.deletions = str(stats.get("deletions", ""))
        row.total_changes = str(stats.get("total", ""))
        row.files_changed = str(len(detail.get("files") or []))
        fetched += 1
    return fetched


def _repo_from_repository_url(url: str) -> str:
    marker = "/repos/"
    if marker in url:
        return url.split(marker, 1)[1]
    return ""


def _fetch_pr_reviews(repo: str, number: int) -> list[dict]:
    reviews: list[dict] = []
    page = 1
    while True:
        batch = gh_api(f"repos/{repo}/pulls/{number}/reviews?per_page=100&page={page}")
        if not isinstance(batch, list):
            return reviews
        reviews.extend(batch)
        if len(batch) < 100:
            return reviews
        page += 1


def scan_reviews(
    email: str,
    login: str,
    scopes: Sequence[str],
    since: str,
    until: str,
    max_prs: int,
    warnings: list[str],
) -> list[ReviewRow]:
    """Find PR reviews by ``login``; exact date filtering happens per review."""
    candidates: dict[tuple[str, int], dict] = {}
    for scope in scopes:
        base_query = f"is:pull-request reviewed-by:{login}"
        if scope:
            base_query += f" {scope}"
        if since != DEFAULT_SINCE:
            # A review inside the window forces the PR's updated_at past it.
            base_query += f" updated:>={since}"
        items = search_all(
            "search/issues",
            base_query,
            "created",
            lambda item: item.get("created_at") or "",
            DEFAULT_SINCE,  # the PR may long predate the review window
            until,
            f"review search for {login}",
            warnings,
        )
        for item in items:
            repo = _repo_from_repository_url(item.get("repository_url") or "")
            number = item.get("number")
            if not repo or number is None:
                continue
            candidates.setdefault((repo, int(number)), item)
    ordered = sorted(
        candidates.items(), key=lambda entry: entry[1].get("created_at") or "", reverse=True
    )
    if len(ordered) > max_prs:
        warnings.append(
            f"{email}: review detail capped to the {max_prs} newest of "
            f"{len(ordered)} reviewed PRs (--max-prs)"
        )
        ordered = ordered[:max_prs]
    rows: list[ReviewRow] = []
    for (repo, number), item in ordered:
        try:
            reviews = _fetch_pr_reviews(repo, number)
        except GhError as exc:
            warnings.append(f"{repo}#{number}: review fetch failed: {exc}")
            continue
        for review in reviews:
            reviewer = ((review.get("user") or {}).get("login")) or ""
            if reviewer.lower() != login.lower():
                continue
            submitted = review.get("submitted_at") or ""
            # Unsubmitted draft reviews (visible only when scanning yourself)
            # have state PENDING and no timestamp — they are not activity.
            if (review.get("state") or "") == "PENDING" or not submitted:
                continue
            day = _utc_day(submitted)
            if day < since or day > until:
                continue
            rows.append(
                ReviewRow(
                    email=email,
                    login=login,
                    repo=repo,
                    pr_number=str(number),
                    pr_title=item.get("title") or "",
                    pr_author=((item.get("user") or {}).get("login")) or "",
                    review_state=review.get("state") or "",
                    submitted_at=submitted,
                    pr_url=item.get("html_url") or "",
                )
            )
    return rows


def write_rows(path: Path, fieldnames: Sequence[str], rows: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
            count += 1
    return count


def _print_email_summary(
    email: str,
    login: str,
    rows: Sequence[CommitRow],
    stats_fetched: int,
    no_stats: bool,
    reviews: Sequence[ReviewRow],
) -> None:
    print(f"{email} -> login: {login or '(unresolved; reviews skipped)'}")
    if rows:
        days = sorted(row.author_date[:10] for row in rows if row.author_date)
        span = f" ({days[0]} .. {days[-1]})" if days else ""
        repo_count = len({row.repo for row in rows})
        stats_note = "stats skipped" if no_stats else f"stats fetched: {stats_fetched}/{len(rows)}"
        print(f"  commits: {len(rows)} across {repo_count} repo(s){span}; {stats_note}")
    else:
        print("  commits: 0")
    if reviews:
        states = Counter(row.review_state for row in reviews)
        breakdown = ", ".join(f"{state} {count}" for state, count in states.most_common())
        pr_count = len({(row.repo, row.pr_number) for row in reviews})
        print(f"  reviews: {len(reviews)} ({breakdown}) across {pr_count} PR(s)")
    else:
        print("  reviews: 0")


def read_emails_file(path: Path) -> list[str]:
    emails: list[str] = []
    # utf-8-sig eats the BOM that Windows editors and older PowerShell
    # redirects prepend, which would otherwise glue itself to the first email.
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            emails.append(line)
    return emails


def parse_iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def parse_repo(value: str) -> str:
    if value.count("/") != 1 or value.startswith("/") or value.endswith("/"):
        raise argparse.ArgumentTypeError(f"expected OWNER/NAME, got {value!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report GitHub commits and pull-request reviews for the given author "
            "emails, via the gh CLI. Writes commits.csv and reviews.csv."
        )
    )
    parser.add_argument("emails", nargs="*", help="git author emails to scan")
    parser.add_argument(
        "--emails-file",
        type=Path,
        help="file with one email per line; blank lines and # comments ignored",
    )
    parser.add_argument(
        "--org",
        action="append",
        default=[],
        metavar="NAME",
        help="restrict to a GitHub organization (repeatable; scopes are unioned)",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        type=parse_repo,
        metavar="OWNER/NAME",
        help="restrict to a repository (repeatable; combines with --org as a union)",
    )
    parser.add_argument("--since", type=parse_iso_date, help="earliest date to include (YYYY-MM-DD)")
    parser.add_argument("--until", type=parse_iso_date, help="latest date to include (YYYY-MM-DD)")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="directory for commits.csv and reviews.csv (default: current directory)",
    )
    parser.add_argument(
        "--max-commits",
        type=int,
        default=DEFAULT_MAX_COMMITS,
        help=f"per-email cap on per-commit stats fetches (default {DEFAULT_MAX_COMMITS})",
    )
    parser.add_argument(
        "--max-prs",
        type=int,
        default=DEFAULT_MAX_PRS,
        help=f"per-email cap on reviewed-PR detail fetches (default {DEFAULT_MAX_PRS})",
    )
    parser.add_argument(
        "--no-stats",
        action="store_true",
        help="skip per-commit stats fetches; magnitude columns stay blank",
    )
    parser.add_argument("--strict", action="store_true", help="exit 2 if any warnings were emitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    file_emails: list[str] = []
    if args.emails_file:
        try:
            file_emails = read_emails_file(args.emails_file)
        except (OSError, ValueError) as exc:  # ValueError covers bad encodings
            parser.error(f"cannot read --emails-file: {exc}")
    emails: list[str] = []
    for email in [*args.emails, *file_emails]:
        if email not in emails:
            emails.append(email)
    if not emails:
        parser.error("no emails given; pass them as arguments or via --emails-file")

    since = args.since or DEFAULT_SINCE
    until = args.until or datetime.now(timezone.utc).date().isoformat()
    if since > until:
        parser.error("--since must not be after --until")
    try:
        # Validate up front: a scan can run for hours, and a bad --out-dir
        # must not surface only when the results are finally written.
        args.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        parser.error(f"cannot create --out-dir: {exc}")
    scopes = build_scopes(args.org, args.repo)

    warnings: list[str] = []
    commit_rows: list[CommitRow] = []
    review_rows: list[ReviewRow] = []
    try:
        gh_api("rate_limit")  # fail fast when gh is missing or unauthenticated
        for email in emails:
            rows, votes = scan_commits(email, scopes, since, until, warnings)
            login = resolve_login(email, votes, warnings)
            for row in rows:
                row.login = login
            stats_fetched = 0
            if rows and not args.no_stats:
                stats_fetched = fetch_commit_stats(email, rows, args.max_commits, warnings)
            reviews: list[ReviewRow] = []
            if login:
                reviews = scan_reviews(
                    email, login, scopes, since, until, args.max_prs, warnings
                )
            commit_rows.extend(rows)
            review_rows.extend(reviews)
            _print_email_summary(email, login, rows, stats_fetched, args.no_stats, reviews)
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    commits_path = args.out_dir / "commits.csv"
    reviews_path = args.out_dir / "reviews.csv"
    try:
        wrote_commits = write_rows(commits_path, COMMIT_FIELDS, commit_rows)
        wrote_reviews = write_rows(reviews_path, REVIEW_FIELDS, review_rows)
    except OSError as exc:
        print(f"error: cannot write results: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {wrote_commits} commit rows -> {commits_path}")
    print(f"wrote {wrote_reviews} review rows -> {reviews_path}")

    if warnings:
        print(f"warnings ({len(warnings)}):", file=sys.stderr)
        for warning in warnings[:MAX_PRINTED_WARNINGS]:
            print(f"  - {warning}", file=sys.stderr)
        if len(warnings) > MAX_PRINTED_WARNINGS:
            print(f"  ... and {len(warnings) - MAX_PRINTED_WARNINGS} more", file=sys.stderr)
        if args.strict:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
