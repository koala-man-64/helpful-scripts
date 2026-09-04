# GitHub keyword search

> Read-only. The script only ever calls `gh api` with GET endpoints — it never
> writes to GitHub, and it never handles a token itself.

Walks a GitHub organization's commit history for keywords and writes a single
self-contained **HTML report** — no CSVs, no external CSS/JS, nothing to
host. There are **no command-line arguments**: everything is configured by
editing constants at the top of the script, then running it.

For each repo in the org (optionally narrowed by a repo-name filter), every
commit in an optional date window — on that repo's default branch — has its
**commit message** and the **added/removed lines of every file it touched**
checked for each keyword. This finds keywords even if they were later
removed or rewritten, not just what's present in the code today. For every
match it logs:

repo · commit URL · branch · commit SHA · commit message · committer ·
commit date · matched keyword · where it matched (commit message or a file)
· a snippet

The report has a live filter box (plain JS, no libraries) and per-keyword /
per-repo summary counts.

All GitHub access goes through the [GitHub CLI](https://cli.github.com/)
(`gh api`), so authentication is whatever `gh auth login` set up — no token,
no `.env`, and zero Python dependencies (stdlib only, Python 3.10+).

## Quickstart

```powershell
gh auth login   # once, if you haven't already
```

Open `github_keyword_search.py` and edit the **CONFIGURATION** block near the
top:

```python
GITHUB_ORG = "my-org"

# Only scan repos whose name contains one of these (case-insensitive).
# Empty = every non-fork repo in the org.
REPO_NAME_KEYWORDS: tuple[str, ...] = ("payments", "infra")

# Inclusive date window, "YYYY-MM-DD". None = unbounded on that side.
SINCE = "2025-01-01"
UNTIL = None  # through today

KEYWORDS: tuple[str, ...] = ("AWS_SECRET", "TODO(security)")
```

Then just run it — no flags, no arguments:

```powershell
py -3 .\github_keyword_search.py
```

That's it. `keyword_search_report.html` lands in the current directory.

## Configuration reference

| Constant | Meaning |
| --- | --- |
| `GITHUB_ORG` | GitHub organization to scan. Required. |
| `REPO_NAME_KEYWORDS` | Tuple of substrings; a repo is scanned if its name contains any of them (case-insensitive). Empty tuple = every repo. |
| `SINCE` / `UNTIL` | Inclusive commit-date window, `"YYYY-MM-DD"`. Either can be `None`. |
| `KEYWORDS` | Tuple of strings to search for (case-insensitive substring match). Required — at least one. |
| `MAX_COMMITS_PER_REPO` | Safety valve per repo (default 500); a warning is recorded if a repo's history in the window exceeds it. |
| `INCLUDE_FORKS` | Default `False` — forked repos duplicate the upstream repo's commit history, so scanning them just produces duplicate rows. |
| `STRICT` | Default `False`. When `True`, the script exits with code 2 if any warnings were recorded (useful for unattended/CI-style runs). |
| `OUT_DIR` / `OUT_FILE` | Where the report is written. |

Exit codes: `0` success, `1` gh/API failure, `2` bad configuration or
(when `STRICT = True`) warnings.

## How it works

1. `orgs/{org}/repos` lists every repo in the org (paginated), then
   `REPO_NAME_KEYWORDS` and `INCLUDE_FORKS` filter it down.
2. `repos/{repo}/commits?sha=BRANCH&since=...&until=...` lists that repo's
   commits on its default branch within the date window (paginated, capped
   at `MAX_COMMITS_PER_REPO`).
3. Each commit's message is checked for every keyword first — free, no
   extra API call. Only if a keyword *isn't* in the message does the script
   fetch `repos/{repo}/commits/{sha}` (once per commit, shared across all
   remaining keywords) and grep the added/removed lines of every changed
   file's diff.
4. Everything is written into one HTML file — no template files, no CDN
   assets, nothing else to ship alongside it.

Every `gh api` call is paced by a small fixed delay (courteous pacing, not a
documented GitHub requirement — see
[GitHub's REST API best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
on avoiding secondary rate limits). This uses the *general* REST API rate
limit (5,000 requests/hour, authenticated), not the much tighter code-search
limit, because this tool walks commit history via plain REST endpoints
rather than GitHub's code-search index. Rate-limit responses (429, or 403s
that mention rate limits) get a fixed 60-second backoff with two retries;
permission 403s fail immediately.

A full history walk makes roughly 1–2 API calls per commit in the window,
per repo — an active repo with thousands of commits in range will take a
while. Narrow `REPO_NAME_KEYWORDS` and the date window, or lower
`MAX_COMMITS_PER_REPO`, to control runtime.

## Caveats

- **Only the default branch is walked.** Other branches' unique commits
  aren't scanned.
- A commit only gets its diff checked when the keyword **isn't already in
  the commit message** — this is an optimization (fewer API calls), not a
  gap: if the message matches, that's already a hit.
- Large commits: GitHub caps the `files` list per commit at 300 entries, and
  omits `patch` text for binary files and very large diffs — a keyword
  inside one of those is not found. Same limitation the sibling
  [github-activity-scanner](../github-activity-scanner/README.md) documents
  for its own per-commit stats fetch.
- `commit_message` in the report is the first line only. `committer` is the
  git-level committer name (`commit.committer.name`), not a resolved GitHub
  login.
- You only see repositories the `gh` account can see; private-repo results
  require a token/account with access to those repos.
- No dedup across keywords within one commit beyond what's described above:
  a commit matching three keywords produces three rows, one per keyword.

## Tests

Fully offline — the gh subprocess seam is faked; no network, no gh binary,
and no real `GITHUB_ORG` needed:

```powershell
py -3 -m unittest discover -s .\tests -v
```
