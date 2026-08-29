# GitHub activity scanner

> Read-only. The script only ever calls `gh api` with GET endpoints — it never
> writes to GitHub, and it never handles a token itself.

Takes a list of author emails and reports their GitHub activity as two CSVs:

- **commits.csv** — every indexed commit authored under each email: repo, SHA,
  author/committer dates, first line of the commit message, additions,
  deletions, total changed lines, files changed, and a link.
- **reviews.csv** — every pull-request review left by the GitHub account that
  matches each email: repo, PR number/title/author, review state
  (`APPROVED` / `CHANGES_REQUESTED` / `COMMENTED` / `DISMISSED`), and the
  submission timestamp. Unsubmitted `PENDING` drafts are excluded. Approvals
  are just a filter on `review_state == APPROVED`.

All GitHub access goes through the [GitHub CLI](https://cli.github.com/)
(`gh api`), so authentication is whatever `gh auth login` set up — no token,
no `.env`, and zero Python dependencies (stdlib only, Python 3.10+).

## Quickstart

```powershell
gh auth login   # once, if you haven't already

# One email, all of public GitHub, last ~18 years:
py -3 .\github_activity_scanner.py somebody@example.com

# Several emails from a file, limited to an org and a date window:
py -3 .\github_activity_scanner.py --emails-file .\emails.txt --org my-org --since 2025-01-01

# Fast pass without per-commit magnitude (1 API call per commit saved):
py -3 .\github_activity_scanner.py somebody@example.com --no-stats --out-dir .\out
```

`emails.txt` is one email per line; blank lines and `#` comments are ignored.

## Flags

| Flag | Meaning |
| --- | --- |
| `EMAIL ...` | git author emails to scan (positional, any number) |
| `--emails-file PATH` | file of emails, merged and deduped with positionals |
| `--org NAME` | restrict to an organization; repeatable, scopes are unioned |
| `--repo OWNER/NAME` | restrict to a repository; repeatable, combines with `--org` |
| `--since` / `--until` | inclusive date window (`YYYY-MM-DD`); default 2008-01-01 .. today |
| `--out-dir PATH` | where `commits.csv` / `reviews.csv` land (default: current dir) |
| `--max-commits N` | per-email cap on per-commit stats fetches (default 300, newest first) |
| `--max-prs N` | per-email cap on reviewed-PR detail fetches (default 200, newest first) |
| `--no-stats` | skip stats fetches entirely; magnitude columns stay blank |
| `--strict` | exit 2 if any warnings were emitted |

Exit codes: `0` success, `1` gh/API failure, `2` bad arguments or
(`--strict`) warnings.

## How it works

1. `search/commits?q=author-email:EMAIL` finds commits (one query per scope,
   paginated, sliced by date window whenever a query would exceed GitHub's
   1,000-results-per-query cap).
2. The commit results vote on the matching GitHub login (an email registered
   to an account shows up as `author.login`); if no commit reveals one,
   `search/users?q=EMAIL in:email` is tried, accepted only on a unique match.
3. `repos/{repo}/commits/{sha}` fills in additions/deletions per commit
   (skippable, capped).
4. `search/issues?q=is:pull-request reviewed-by:LOGIN` lists reviewed PRs,
   then `repos/{o}/{r}/pulls/{n}/reviews` yields the exact review records —
   only the target account's rows are kept.

Searches are throttled to stay under GitHub's 30 searches/minute limit, so
large unscoped scans are slow by design. Rate-limit responses (429, or 403s
that mention rate limits) get a fixed 60-second backoff with two retries;
permission 403s fail immediately. Date-window slicing works on UTC days.

## Caveats

- Commit search only covers the **default branch** of indexed, non-fork
  repositories, and unscoped queries sample at most ~4,000 matching
  repositories. Scoped runs (`--org`/`--repo`) are far more complete.
- You only see repositories the `gh` account can see. Private-repo activity
  requires a token with access to those repos.
- People who commit under GitHub **noreply** addresses (or several personal
  emails) are only found for the exact emails you pass — pass all known
  variants.
- Reviews require the email to resolve to a login; when it can't be resolved
  uniquely, commits are still reported and reviews are skipped with a warning.
- `files_changed` undercounts commits touching more than 300 files (GitHub
  caps the file list; the additions/deletions totals stay correct).
- `message` is the first line only. Timestamps are reported verbatim from the
  API (author vs committer date both included).
- More than 1,000 search results on a single **day** cannot be paged past;
  the day is truncated with a warning.
- GitHub search can time out server-side and return partial results; the tool
  emits a warning when GitHub reports that (`--strict` turns it into exit 2).
- No cross-email identity merging: each email is scanned and reported
  independently, even when two emails map to the same account.

## Tests

Fully offline — the gh subprocess seam is faked; no network, no gh binary
needed:

```powershell
py -3 -m unittest discover -s .\tests -v
```
