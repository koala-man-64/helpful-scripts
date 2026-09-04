#!/usr/bin/env python3
"""Scan a GitHub org's commit history for keywords, no arguments required.

Edit the CONFIGURATION block below (org, optional repo-name filter, optional
date range, keywords), then run:

    py -3 github_keyword_search.py

For every repo in the org whose name matches the filter, every commit in the
date window (on the repo's default branch) has its message and its diff
(added/removed lines) checked for each keyword. A match logs: repo, URL,
branch, commit SHA, commit message, committer, commit date, the keyword
found, where it was found (commit message or a file), and a snippet. Results
are written to a single self-contained HTML report.

The script is read-only and dependency-free: all GitHub access goes through
the GitHub CLI (``gh api``), so authentication comes from ``gh auth login``
and no token ever touches this file.
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


# ============================================================================
# CONFIGURATION — edit these, then run:  py -3 github_keyword_search.py
# No command-line arguments are needed or supported; everything lives here.
# ============================================================================

# GitHub organization to scan. Required.
GITHUB_ORG = "your-org"

# Only scan repos whose name contains one of these substrings
# (case-insensitive). Empty = scan every non-fork repo in the org.
REPO_NAME_KEYWORDS: tuple[str, ...] = ()

# Inclusive commit-date window, "YYYY-MM-DD". None = no bound on that side
# (SINCE=None scans from the repo's first commit; UNTIL=None scans through
# today).
SINCE: str | None = None
UNTIL: str | None = None

# Keywords to search for (case-insensitive substring match) in each commit's
# message and in the added/removed lines of every file it touched.
KEYWORDS: tuple[str, ...] = ("TODO(security)", "API_KEY")

# Safety valve: stop walking a repo's history after this many commits in the
# window, so one huge/ancient repo can't turn an unattended run into an
# unbounded one. A warning is recorded when this is hit.
MAX_COMMITS_PER_REPO = 500

# Skip forked repos — their history duplicates the upstream repo's commits,
# so scanning them just produces duplicate rows.
INCLUDE_FORKS = False

# Exit with code 2 (instead of 0) if any warnings were recorded. Useful when
# this runs unattended and you want a non-zero exit to be noticed.
STRICT = False

# Where the report is written.
OUT_DIR = Path(".")
OUT_FILE = "keyword_search_report.html"

# ============================================================================
# End of configuration.
# ============================================================================


SNIPPET_MAX_LEN = 240
SNIPPET_CONTEXT_CHARS = 60
# GitHub's general REST API allows far more throughput than the search API
# (5000 req/hr authenticated), but a full history walk can still make
# thousands of sequential calls; this is a courteous pace to stay well clear
# of secondary rate limits, not a documented per-request GitHub requirement.
# https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
GENERAL_API_SLEEP_SECONDS = 0.25
RATE_LIMIT_SLEEP_SECONDS = 60.0
MAX_RATE_LIMIT_RETRIES = 2
MAX_PRINTED_WARNINGS = 10


class GhError(RuntimeError):
    """A gh invocation failed in a way the scan cannot recover from."""


@dataclass(frozen=True)
class Config:
    org: str
    repo_name_keywords: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None
    keywords: tuple[str, ...] = ()
    max_commits_per_repo: int = MAX_COMMITS_PER_REPO
    include_forks: bool = False
    strict: bool = False
    out_dir: Path = Path(".")
    out_file: str = "keyword_search_report.html"

    def validate(self) -> list[str]:
        errors = []
        if not self.org.strip():
            errors.append("GITHUB_ORG is empty; set it to your GitHub organization login")
        if not self.keywords:
            errors.append("KEYWORDS is empty; add at least one keyword to search for")
        for value, name in ((self.since, "SINCE"), (self.until, "UNTIL")):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    errors.append(f"{name} = {value!r} is not a valid YYYY-MM-DD date")
        if self.since and self.until and self.since > self.until:
            errors.append(f"SINCE ({self.since}) is after UNTIL ({self.until})")
        if self.max_commits_per_repo <= 0:
            errors.append("MAX_COMMITS_PER_REPO must be positive")
        return errors


def default_config() -> Config:
    """Build a Config from the module-level constants above."""
    return Config(
        org=GITHUB_ORG,
        repo_name_keywords=tuple(REPO_NAME_KEYWORDS),
        since=SINCE,
        until=UNTIL,
        keywords=tuple(KEYWORDS),
        max_commits_per_repo=MAX_COMMITS_PER_REPO,
        include_forks=INCLUDE_FORKS,
        strict=STRICT,
        out_dir=OUT_DIR,
        out_file=OUT_FILE,
    )


@dataclass
class MatchRow:
    keyword: str
    repo: str
    url: str
    branch: str
    commit_sha: str
    commit_message: str
    committer: str
    commit_date: str
    matched_in: str
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


def gh_api(path: str) -> Any:
    """Call ``gh api PATH`` and return the parsed JSON response.

    Every call is paced (see GENERAL_API_SLEEP_SECONDS). 403/429 responses
    get a fixed sleep-and-retry; 401 becomes a clear "run gh auth login"
    error.
    """
    _sleep(GENERAL_API_SLEEP_SECONDS)
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


def list_org_repos(org: str) -> list[dict]:
    """Every repo in ``org`` visible to the authenticated gh account."""
    repos: list[dict] = []
    page = 1
    while True:
        batch = gh_api(f"orgs/{org}/repos?per_page=100&page={page}&type=all")
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def filter_repos(repos: list[dict], name_keywords: tuple[str, ...], include_forks: bool) -> list[dict]:
    lowered_keywords = [kw.lower() for kw in name_keywords]
    result = []
    for repo in repos:
        if not include_forks and repo.get("fork"):
            continue
        name = repo.get("name") or ""
        if lowered_keywords and not any(kw in name.lower() for kw in lowered_keywords):
            continue
        result.append(repo)
    return result


def list_commits(
    repo_full_name: str,
    branch: str,
    since: str | None,
    until: str | None,
    max_commits: int,
    warnings: list[str],
) -> list[dict]:
    """Commits on ``branch`` within the inclusive [since, until] day window."""
    params: dict[str, str] = {"sha": branch, "per_page": "100"}
    if since:
        params["since"] = f"{since}T00:00:00Z"
    if until:
        params["until"] = f"{until}T23:59:59Z"
    commits: list[dict] = []
    page = 1
    while True:
        params["page"] = str(page)
        query = urllib.parse.urlencode(params)
        batch = gh_api(f"repos/{repo_full_name}/commits?{query}")
        if not isinstance(batch, list) or not batch:
            break
        commits.extend(batch)
        if len(commits) >= max_commits:
            warnings.append(
                f"{repo_full_name}: MAX_COMMITS_PER_REPO={max_commits} reached; "
                f"older commits in the window were not scanned"
            )
            del commits[max_commits:]
            break
        if len(batch) < 100:
            break
        page += 1
    return commits


def fetch_commit_detail(repo_full_name: str, sha: str, warnings: list[str]) -> dict | None:
    try:
        return gh_api(f"repos/{repo_full_name}/commits/{sha}")
    except GhError as exc:
        warnings.append(f"{repo_full_name}@{sha[:7]}: could not fetch commit detail: {exc}")
        return None


def extract_message_snippet(message: str, keyword: str) -> str:
    lower = message.lower()
    idx = lower.find(keyword.lower())
    if idx == -1:
        text = message
        prefix = suffix = ""
    else:
        start = max(0, idx - SNIPPET_CONTEXT_CHARS)
        end = min(len(message), idx + len(keyword) + SNIPPET_CONTEXT_CHARS)
        text = message[start:end]
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(message) else ""
    collapsed = " ".join(text.split())
    if len(collapsed) > SNIPPET_MAX_LEN:
        collapsed = collapsed[: SNIPPET_MAX_LEN - 1].rstrip() + "…"
    return prefix + collapsed + suffix


def clean_diff_line(line: str) -> str:
    marker = "+ " if line[:1] == "+" else ("- " if line[:1] == "-" else "")
    text = marker + line[1:].strip()
    if len(text) > SNIPPET_MAX_LEN:
        text = text[: SNIPPET_MAX_LEN - 1].rstrip() + "…"
    return text


def find_diff_match(keyword: str, commit_detail: dict | None) -> tuple[str, str] | None:
    """The first file (and diff line) in ``commit_detail`` whose patch contains ``keyword``."""
    if not commit_detail:
        return None
    needle = keyword.lower()
    matched_files: list[tuple[str, str]] = []
    for file_entry in commit_detail.get("files") or []:
        patch = file_entry.get("patch")
        if not patch:
            continue  # binary files and very large diffs have no patch text
        for line in patch.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line[:1] in "+-" and needle in line.lower():
                matched_files.append((file_entry.get("filename") or "(unknown file)", clean_diff_line(line)))
                break
    if not matched_files:
        return None
    filename, snippet = matched_files[0]
    if len(matched_files) > 1:
        filename = f"{filename} (+{len(matched_files) - 1} more file(s))"
    return filename, snippet


def build_row(keyword: str, repo_full_name: str, branch: str, commit_summary: dict, matched_in: str, snippet: str) -> MatchRow:
    commit_info = commit_summary.get("commit") or {}
    message_lines = (commit_info.get("message") or "").splitlines()
    committer_info = commit_info.get("committer") or {}
    return MatchRow(
        keyword=keyword,
        repo=repo_full_name,
        url=commit_summary.get("html_url") or f"https://github.com/{repo_full_name}/commit/{commit_summary.get('sha', '')}",
        branch=branch,
        commit_sha=commit_summary.get("sha") or "",
        commit_message=message_lines[0] if message_lines else "",
        committer=committer_info.get("name") or "",
        commit_date=committer_info.get("date") or "",
        matched_in=matched_in,
        snippet=snippet,
    )


def scan_repo(repo: dict, config: Config, warnings: list[str]) -> tuple[list[MatchRow], int]:
    """Walk one repo's commit history and return (matches, commits scanned)."""
    full_name = repo.get("full_name") or ""
    branch = repo.get("default_branch") or ""
    if not full_name or not branch:
        warnings.append(f"{full_name or '(unnamed repo)'}: missing name or default branch; skipped")
        return [], 0
    try:
        commits = list_commits(full_name, branch, config.since, config.until, config.max_commits_per_repo, warnings)
    except GhError as exc:
        warnings.append(f"{full_name}: could not list commits: {exc}")
        return [], 0

    rows: list[MatchRow] = []
    for commit_summary in commits:
        sha = commit_summary.get("sha") or ""
        message = ((commit_summary.get("commit") or {}).get("message")) or ""
        message_lower = message.lower()
        message_hits = {kw for kw in config.keywords if kw.lower() in message_lower}
        remaining = [kw for kw in config.keywords if kw not in message_hits]

        commit_detail = None
        if remaining and sha:
            commit_detail = fetch_commit_detail(full_name, sha, warnings)

        for keyword in config.keywords:
            if keyword in message_hits:
                matched_in = "commit message"
                snippet = extract_message_snippet(message, keyword)
            else:
                result = find_diff_match(keyword, commit_detail)
                if result is None:
                    continue
                matched_in, snippet = result
            rows.append(build_row(keyword, full_name, branch, commit_summary, matched_in, snippet))
    return rows, len(commits)


def scan(config: Config) -> tuple[list[MatchRow], list[str], int, int]:
    """Run the full scan; returns (rows, warnings, repos_scanned, commits_scanned)."""
    warnings: list[str] = []
    gh_api("rate_limit")  # fail fast when gh is missing or unauthenticated
    all_repos = list_org_repos(config.org)
    repos = filter_repos(all_repos, config.repo_name_keywords, config.include_forks)
    if not repos:
        warnings.append(
            f"no repos matched org={config.org!r} name-keywords={config.repo_name_keywords!r} "
            f"include_forks={config.include_forks} ({len(all_repos)} repo(s) in the org before filtering)"
        )

    rows: list[MatchRow] = []
    commits_scanned = 0
    for repo in repos:
        repo_rows, repo_commit_count = scan_repo(repo, config, warnings)
        rows.extend(repo_rows)
        commits_scanned += repo_commit_count
        print(f"{repo.get('full_name', '?')}: {repo_commit_count} commit(s) scanned, {len(repo_rows)} match(es)")
    return rows, warnings, len(repos), commits_scanned


ROW_TEMPLATE = """      <tr>
        <td>{keyword}</td>
        <td><a href="https://github.com/{repo}">{repo}</a></td>
        <td>{branch}</td>
        <td class="mono"><a href="{url}">{short_sha}</a></td>
        <td>{message}</td>
        <td>{committer}</td>
        <td>{commit_date}</td>
        <td class="mono">{matched_in}</td>
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
    config: Config,
    rows: list[MatchRow],
    warnings: list[str],
    repos_scanned: int,
    commits_scanned: int,
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
            branch=esc(row.branch) or "&mdash;",
            url=esc(row.url),
            short_sha=esc(row.commit_sha[:7]) if row.commit_sha else "(unknown)",
            message=esc(row.commit_message) or "&mdash;",
            committer=esc(row.committer) or "&mdash;",
            commit_date=esc(row.commit_date) or "&mdash;",
            matched_in=esc(row.matched_in),
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

    window = f"{config.since or 'repo start'} .. {config.until or 'now'}"
    repo_filter = ", ".join(config.repo_name_keywords) if config.repo_name_keywords else "(all repos)"

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
  <p class="meta">Generated {esc(generated_at)} &middot; org: <strong>{esc(config.org)}</strong> &middot;
    repo filter: {esc(repo_filter)} &middot; date window: {esc(window)} &middot;
    keywords: {" ".join(f'<span class="chip">{esc(k)}</span>' for k in config.keywords)}<br>
    scanned {repos_scanned} repo(s), {commits_scanned} commit(s) &middot;
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
  <input id="filter" type="search" placeholder="Filter matches (repo, message, keyword...)" oninput="filterRows()">
  <table class="matches">
    <thead>
      <tr>
        <th>Keyword</th><th>Repo</th><th>Branch</th><th>Commit</th>
        <th>Message</th><th>Committer</th><th>Commit date</th><th>Matched in</th><th>Snippet</th>
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


def main(config: Config | None = None) -> int:
    cfg = config or default_config()
    errors = cfg.validate()
    if errors:
        for error in errors:
            print(f"config error: {error}", file=sys.stderr)
        return 2

    try:
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create OUT_DIR: {exc}", file=sys.stderr)
        return 2

    try:
        rows, warnings, repos_scanned, commits_scanned = scan(cfg)
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows.sort(key=lambda row: (row.repo, row.commit_date, row.keyword))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_html = render_report(cfg, rows, warnings, repos_scanned, commits_scanned, generated_at)
    report_path = cfg.out_dir / cfg.out_file
    try:
        write_report(report_path, report_html)
    except OSError as exc:
        print(f"error: cannot write report: {exc}", file=sys.stderr)
        return 1

    repo_count = len({row.repo for row in rows})
    print(f"scanned {repos_scanned} repo(s), {commits_scanned} commit(s)")
    print(f"found {len(rows)} match(es) across {repo_count} repo(s)")
    print(f"wrote report -> {report_path}")

    if warnings:
        print(f"warnings ({len(warnings)}):", file=sys.stderr)
        for warning in warnings[:MAX_PRINTED_WARNINGS]:
            print(f"  - {warning}", file=sys.stderr)
        if len(warnings) > MAX_PRINTED_WARNINGS:
            print(f"  ... and {len(warnings) - MAX_PRINTED_WARNINGS} more", file=sys.stderr)
        if cfg.strict:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
