#!/usr/bin/env python3
"""Scan GitHub organizations/repositories for keywords in file contents.

Given one or more keywords, the script searches indexed source code (via
GitHub's code-search API) for each keyword within one or more orgs/repos, and
for every match resolves the last commit that touched the matching file. It
writes a single self-contained HTML report: repo, file, branch, commit SHA,
commit message, committer, commit date, matched keyword, and a highlighted
snippet.

The script is read-only and dependency-free: all GitHub access goes through
the GitHub CLI (``gh api``), so authentication comes from ``gh auth login``
and no token ever touches this file.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


SEARCH_PAGE_SIZE = 100
MAX_SEARCH_RESULTS = 1000  # GitHub returns at most 1000 results per search query
MAX_SEARCH_PAGES = 10
# Code search has its own, much tighter rate limit than other search
# endpoints: 10 requests/minute (authenticated), vs. 30/minute elsewhere.
# https://docs.github.com/en/rest/search/search#rate-limit
SEARCH_CODE_SLEEP_SECONDS = 6.5
RATE_LIMIT_SLEEP_SECONDS = 60.0
MAX_RATE_LIMIT_RETRIES = 2
DEFAULT_MAX_FILES = 500
MAX_PRINTED_WARNINGS = 10
SNIPPET_MAX_LEN = 240
TEXT_MATCH_ACCEPT_HEADER = "Accept: application/vnd.github.text-match+json"


class GhError(RuntimeError):
    """A gh invocation failed in a way the scan cannot recover from."""


@dataclass
class MatchRow:
    keyword: str
    repo: str
    path: str
    url: str
    branch: str
    commit_sha: str
    commit_message: str
    committer: str
    commit_date: str
    snippet: str


_sleep = time.sleep


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


def gh_api(path: str, headers: Sequence[str] = ()) -> Any:
    """Call ``gh api PATH`` (optionally with extra headers) and return parsed JSON.

    Search endpoints are throttled below GitHub's rate limits. 403/429
    responses get a fixed sleep-and-retry; 401 becomes a clear "run gh auth
    login" error.
    """
    if path.startswith("search/"):
        _sleep(SEARCH_CODE_SLEEP_SECONDS)
    argv = ["api"]
    for header in headers:
        argv += ["-H", header]
    argv.append(path)
    retries = 0
    while True:
        code, stdout, stderr = _run_gh(argv)
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


def build_scopes(orgs: Sequence[str], repos: Sequence[str]) -> list[str]:
    """One scope qualifier per query: code search ANDs repeated qualifiers."""
    return [f"org:{org}" for org in orgs] + [f"repo:{repo}" for repo in repos]


def search_keyword(keyword: str, scope: str, warnings: list[str]) -> list[dict]:
    """Find files whose content matches ``keyword`` (exact phrase) within ``scope``."""
    query = f'"{keyword}" {scope}'
    encoded = urllib.parse.quote(query, safe="")
    items: list[dict] = []
    total = 0
    for page in range(1, MAX_SEARCH_PAGES + 1):
        path = f"search/code?q={encoded}&per_page={SEARCH_PAGE_SIZE}&page={page}"
        payload = gh_api(path, headers=(TEXT_MATCH_ACCEPT_HEADER,))
        total = int(payload.get("total_count") or 0)
        batch = payload.get("items") or []
        items.extend(batch)
        if payload.get("incomplete_results"):
            warnings.append(
                f'"{keyword}" {scope}: GitHub reported incomplete results '
                f"(search timed out); matches may be missing"
            )
        if len(batch) < SEARCH_PAGE_SIZE or len(items) >= min(total, MAX_SEARCH_RESULTS):
            break
    if total > MAX_SEARCH_RESULTS:
        warnings.append(
            f'"{keyword}" {scope}: {total} matches exceeds GitHub\'s '
            f"{MAX_SEARCH_RESULTS}-result search cap; only the first "
            f"{len(items)} are included"
        )
    return items


def extract_snippet(item: dict, max_len: int = SNIPPET_MAX_LEN) -> str:
    """Join the highlighted text-match fragments into one readable snippet."""
    matches = item.get("text_matches") or []
    fragments = [match.get("fragment", "") for match in matches if match.get("fragment")]
    text = " … ".join(fragments)
    text = " ".join(text.split())  # collapse newlines/repeated whitespace
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def repo_default_branch(repo: str, cache: dict[str, str], warnings: list[str]) -> str:
    """The repository's default branch (code search only ever matches this branch)."""
    if repo in cache:
        return cache[repo]
    try:
        info = gh_api(f"repos/{repo}")
        branch = info.get("default_branch") or ""
    except GhError as exc:
        warnings.append(f"{repo}: could not fetch default branch: {exc}")
        branch = ""
    cache[repo] = branch
    return branch


def latest_commit_for_path(
    repo: str,
    path: str,
    branch: str,
    cache: dict[tuple[str, str], dict | None],
    warnings: list[str],
) -> dict | None:
    """The most recent commit that touched ``path`` on ``branch`` (None on failure)."""
    key = (repo, path)
    if key in cache:
        return cache[key]
    params = {"path": path, "per_page": "1"}
    if branch:
        params["sha"] = branch
    query = urllib.parse.urlencode(params)
    try:
        commits = gh_api(f"repos/{repo}/commits?{query}")
        commit = commits[0] if isinstance(commits, list) and commits else None
    except GhError as exc:
        warnings.append(f"{repo}/{path}: could not fetch commit history: {exc}")
        commit = None
    cache[key] = commit
    return commit


def build_row(keyword: str, repo: str, path: str, branch: str, commit: dict | None, item: dict) -> MatchRow:
    if commit:
        sha = commit.get("sha") or ""
        commit_info = commit.get("commit") or {}
        message_lines = (commit_info.get("message") or "").splitlines()
        message = message_lines[0] if message_lines else ""
        committer_info = commit_info.get("committer") or {}
        committer = committer_info.get("name") or ""
        commit_date = committer_info.get("date") or ""
    else:
        sha = message = committer = commit_date = ""
    url = item.get("html_url") or (
        f"https://github.com/{repo}/blob/{sha or branch or 'HEAD'}/{path}"
    )
    return MatchRow(
        keyword=keyword,
        repo=repo,
        path=path,
        url=url,
        branch=branch,
        commit_sha=sha,
        commit_message=message,
        committer=committer,
        commit_date=commit_date,
        snippet=extract_snippet(item),
    )


def read_lines_file(path: Path) -> list[str]:
    values: list[str] = []
    # utf-8-sig eats the BOM that Windows editors and older PowerShell
    # redirects prepend, which would otherwise glue itself to the first entry.
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            values.append(line)
    return values


def parse_repo(value: str) -> str:
    if value.count("/") != 1 or value.startswith("/") or value.endswith("/"):
        raise argparse.ArgumentTypeError(f"expected OWNER/NAME, got {value!r}")
    return value


ROW_TEMPLATE = """      <tr>
        <td>{keyword}</td>
        <td><a href="https://github.com/{repo}">{repo}</a></td>
        <td class="mono">{path}</td>
        <td>{branch}</td>
        <td class="mono"><a href="{url}">{short_sha}</a></td>
        <td>{message}</td>
        <td>{committer}</td>
        <td>{commit_date}</td>
        <td class="mono snippet">{snippet}</td>
      </tr>"""

STYLE = """  <style>
    :root { color-scheme: light dark; --bg: #ffffff; --fg: #1b1f23; --muted: #6b7280;
      --border: #d0d7de; --stripe: #f6f8fa; --link: #0969da; --chip-bg: #eef2ff; }
    @media (prefers-color-scheme: dark) {
      :root { --bg: #0d1117; --fg: #e6edf3; --muted: #8b949e; --border: #30363d;
        --stripe: #161b22; --link: #58a6ff; --chip-bg: #1c2333; }
    }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
      font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; }
    h1 { font-size: 1.4rem; margin: 0 0 4px; }
    .meta { color: var(--muted); font-size: 0.85rem; margin: 0 0 20px; }
    .summary { display: flex; flex-wrap: wrap; gap: 24px; margin-bottom: 20px; }
    .summary table { border-collapse: collapse; font-size: 0.85rem; }
    .summary caption { text-align: left; font-weight: 600; margin-bottom: 6px; }
    .summary td { padding: 2px 10px 2px 0; }
    .chip { display: inline-block; background: var(--chip-bg); border-radius: 10px;
      padding: 1px 8px; font-size: 0.8rem; }
    .warnings { border: 1px solid #d29922; background: var(--chip-bg); border-radius: 6px;
      padding: 8px 14px; margin-bottom: 20px; font-size: 0.85rem; }
    .warnings li { margin: 2px 0; }
    #filter { width: 100%; max-width: 420px; padding: 6px 10px; margin-bottom: 12px;
      border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--fg); }
    table.matches { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    table.matches thead th { position: sticky; top: 0; background: var(--bg);
      text-align: left; border-bottom: 2px solid var(--border); padding: 6px 8px; }
    table.matches td { border-bottom: 1px solid var(--border); padding: 6px 8px; vertical-align: top; }
    table.matches tbody tr:nth-child(even) { background: var(--stripe); }
    .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 0.8rem; }
    .snippet { color: var(--muted); max-width: 420px; }
    a { color: var(--link); text-decoration: none; }
    a:hover { text-decoration: underline; }
    #empty-note { display: none; color: var(--muted); padding: 12px 0; }
  </style>"""

SCRIPT = """  <script>
    function filterRows() {
      var q = document.getElementById('filter').value.toLowerCase();
      var rows = document.querySelectorAll('table.matches tbody tr');
      var visible = 0;
      rows.forEach(function (tr) {
        var match = tr.textContent.toLowerCase().indexOf(q) !== -1;
        tr.style.display = match ? '' : 'none';
        if (match) visible++;
      });
      document.getElementById('empty-note').style.display = visible === 0 ? 'block' : 'none';
    }
  </script>"""


def render_report(
    keywords: Sequence[str],
    scopes: Sequence[str],
    rows: Sequence[MatchRow],
    warnings: Sequence[str],
    generated_at: str,
) -> str:
    esc = html.escape
    keyword_counts = Counter(row.keyword for row in rows)
    repo_counts = Counter(row.repo for row in rows)

    keyword_summary_rows = "\n".join(
        f"      <tr><td>{esc(keyword)}</td><td>{count}</td></tr>"
        for keyword, count in sorted(keyword_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ) or "      <tr><td colspan=\"2\">no matches</td></tr>"
    repo_summary_rows = "\n".join(
        f"      <tr><td>{esc(repo)}</td><td>{count}</td></tr>"
        for repo, count in sorted(repo_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ) or "      <tr><td colspan=\"2\">no matches</td></tr>"

    body_rows = "\n".join(
        ROW_TEMPLATE.format(
            keyword=esc(row.keyword),
            repo=esc(row.repo),
            path=esc(row.path),
            branch=esc(row.branch) or "&mdash;",
            url=esc(row.url),
            short_sha=esc(row.commit_sha[:7]) if row.commit_sha else "(unknown)",
            message=esc(row.commit_message) or "&mdash;",
            committer=esc(row.committer) or "&mdash;",
            commit_date=esc(row.commit_date) or "&mdash;",
            snippet=esc(row.snippet),
        )
        for row in rows
    )

    warnings_block = ""
    if warnings:
        items = "\n".join(f"      <li>{esc(warning)}</li>" for warning in warnings)
        warnings_block = f"""  <div class="warnings">
    <strong>{len(warnings)} warning(s):</strong>
    <ul>
{items}
    </ul>
  </div>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GitHub keyword search report</title>
{STYLE}
</head>
<body>
  <h1>GitHub keyword search report</h1>
  <p class="meta">Generated {esc(generated_at)} &middot;
    keywords: {" ".join(f'<span class="chip">{esc(k)}</span>' for k in keywords)} &middot;
    scopes: {", ".join(esc(s) for s in scopes)} &middot;
    {len(rows)} match(es) across {len(repo_counts)} repo(s)</p>
{warnings_block}  <div class="summary">
    <table>
      <caption>Matches by keyword</caption>
      <tbody>
{keyword_summary_rows}
      </tbody>
    </table>
    <table>
      <caption>Matches by repo</caption>
      <tbody>
{repo_summary_rows}
      </tbody>
    </table>
  </div>
  <input id="filter" type="search" placeholder="Filter matches (repo, path, keyword, message...)" oninput="filterRows()">
  <table class="matches">
    <thead>
      <tr>
        <th>Keyword</th><th>Repo</th><th>Path</th><th>Branch</th><th>Commit</th>
        <th>Message</th><th>Committer</th><th>Commit date</th><th>Snippet</th>
      </tr>
    </thead>
    <tbody>
{body_rows}
    </tbody>
  </table>
  <p id="empty-note">No matches for this filter.</p>
{SCRIPT}
</body>
</html>
"""


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Search GitHub organizations/repositories for keywords in file "
            "contents, via the gh CLI, and write a single self-contained "
            "HTML report."
        )
    )
    parser.add_argument("keywords", nargs="*", help="keywords to search for (exact phrase each)")
    parser.add_argument(
        "--keywords-file",
        type=Path,
        help="file with one keyword per line; blank lines and # comments ignored",
    )
    parser.add_argument(
        "--org",
        action="append",
        default=[],
        metavar="NAME",
        help="GitHub organization to search (repeatable; scopes are unioned)",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        type=parse_repo,
        metavar="OWNER/NAME",
        help="repository to search (repeatable; combines with --org as a union)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="directory for the HTML report (default: current directory)",
    )
    parser.add_argument(
        "--out-file",
        type=Path,
        default=Path("keyword_search_report.html"),
        help="report filename, relative to --out-dir (default: keyword_search_report.html)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help=f"cap on distinct files enriched with commit details (default {DEFAULT_MAX_FILES})",
    )
    parser.add_argument("--strict", action="store_true", help="exit 2 if any warnings were emitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    file_keywords: list[str] = []
    if args.keywords_file:
        try:
            file_keywords = read_lines_file(args.keywords_file)
        except (OSError, ValueError) as exc:  # ValueError covers bad encodings
            parser.error(f"cannot read --keywords-file: {exc}")
    keywords: list[str] = []
    for keyword in [*args.keywords, *file_keywords]:
        if keyword not in keywords:
            keywords.append(keyword)
    if not keywords:
        parser.error("no keywords given; pass them as arguments or via --keywords-file")
    for keyword in keywords:
        if '"' in keyword:
            parser.error(
                f'keyword {keyword!r} contains a double quote; exact-phrase '
                f"search cannot express that (rephrase without the quote)"
            )

    if not args.org and not args.repo:
        parser.error(
            "at least one --org or --repo is required (code search has no "
            "useful unscoped/global mode for this tool)"
        )

    try:
        # Validate up front: a scan can run for minutes, and a bad --out-dir
        # must not surface only when the report is finally written.
        args.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        parser.error(f"cannot create --out-dir: {exc}")

    scopes = build_scopes(args.org, args.repo)

    warnings: list[str] = []
    rows: list[MatchRow] = []
    branch_cache: dict[str, str] = {}
    commit_cache: dict[tuple[str, str], dict | None] = {}
    enriched = 0
    max_files_warned = False
    try:
        gh_api("rate_limit")  # fail fast when gh is missing or unauthenticated
        for keyword in keywords:
            for scope in scopes:
                items = search_keyword(keyword, scope, warnings)
                for item in items:
                    repo = ((item.get("repository") or {}).get("full_name")) or ""
                    path = item.get("path") or ""
                    if not repo or not path:
                        continue
                    branch = repo_default_branch(repo, branch_cache, warnings)
                    key = (repo, path)
                    if key not in commit_cache and enriched >= args.max_files:
                        if not max_files_warned:
                            max_files_warned = True
                            warnings.append(
                                f"--max-files={args.max_files} reached; some "
                                f"matches are left without commit details"
                            )
                        commit = None
                    else:
                        if key not in commit_cache:
                            enriched += 1
                        commit = latest_commit_for_path(repo, path, branch, commit_cache, warnings)
                    rows.append(build_row(keyword, repo, path, branch, commit, item))
                print(f'"{keyword}" {scope}: {len(items)} match(es)')
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows.sort(key=lambda row: (row.keyword, row.repo, row.path))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_html = render_report(keywords, scopes, rows, warnings, generated_at)
    report_path = args.out_dir / args.out_file
    try:
        write_report(report_path, report_html)
    except OSError as exc:
        print(f"error: cannot write report: {exc}", file=sys.stderr)
        return 1

    repo_count = len({row.repo for row in rows})
    print(f"found {len(rows)} match(es) across {repo_count} repo(s)")
    print(f"wrote report -> {report_path}")

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
