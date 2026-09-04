# GitHub keyword search

> Read-only. The script only ever calls `gh api` with GET endpoints — it never
> writes to GitHub, and it never handles a token itself.

Searches one or more GitHub organizations/repositories for keywords in file
contents (via GitHub's code-search API) and writes a single self-contained
**HTML report** — no CSVs, no external CSS/JS, nothing to host. For every
match it logs:

repo · file URL · branch · commit SHA · commit message · committer · commit
date · matched keyword · a highlighted snippet of the match

The report has a live filter box (plain JS, no libraries) and per-keyword /
per-repo summary counts.

All GitHub access goes through the [GitHub CLI](https://cli.github.com/)
(`gh api`), so authentication is whatever `gh auth login` set up — no token,
no `.env`, and zero Python dependencies (stdlib only, Python 3.10+).

## Quickstart

```powershell
gh auth login   # once, if you haven't already

# One keyword, one org:
py -3 .\github_keyword_search.py "AWS_SECRET" --org my-org

# Several keywords from a file, across an org and one extra repo:
py -3 .\github_keyword_search.py --keywords-file .\keywords.txt --org my-org --repo other-org/some-repo

# Custom output location:
py -3 .\github_keyword_search.py "password" --org my-org --out-dir .\out --out-file secrets_scan.html
```

`keywords.txt` is one keyword per line; blank lines and `#` comments are
ignored. Each keyword is searched as an **exact phrase** (quoted internally),
so `TODO(security)` matches that literal string, not each word separately.
Keywords containing a literal `"` are rejected — there's no way to express
that in an exact-phrase query.

## Flags

| Flag | Meaning |
| --- | --- |
| `KEYWORD ...` | keywords to search for (positional, any number) |
| `--keywords-file PATH` | file of keywords, merged and deduped with positionals |
| `--org NAME` | GitHub organization to search; repeatable, scopes are unioned |
| `--repo OWNER/NAME` | repository to search; repeatable, combines with `--org` |
| `--out-dir PATH` | directory for the report (default: current dir) |
| `--out-file NAME` | report filename (default: `keyword_search_report.html`) |
| `--max-files N` | cap on distinct files enriched with commit details (default 500) |
| `--strict` | exit 2 if any warnings were emitted |

At least one `--org` or `--repo` is required — GitHub's code-search API has
no scope-limited "search everything" mode worth using here, and an unscoped
query would be slow, noisy, and hit the 4,000-repository search-scope limit
long before it found anything you actually wanted.

Exit codes: `0` success, `1` gh/API failure, `2` bad arguments or
(`--strict`) warnings.

## How it works

1. `search/code?q="KEYWORD" SCOPE` (one query per keyword × scope) finds
   matching files, requesting the `text-match` media type so GitHub returns
   highlighted fragments.
2. `repos/{repo}` resolves each repo's default branch (cached per repo).
3. `repos/{repo}/commits?path=FILE&sha=BRANCH&per_page=1` resolves the most
   recent commit that touched each matched file (cached per repo+path, so a
   file matching multiple keywords costs one lookup, not one per keyword).
4. Everything is written into one HTML file — no template files, no CDN
   assets, nothing else to ship alongside it.

Search calls are throttled to stay under GitHub's **10 requests/minute**
limit for the code-search endpoint specifically (other search endpoints get
30/minute; code search does not — see
[GitHub's rate-limit docs](https://docs.github.com/en/rest/search/search#rate-limit)).
Rate-limit responses (429, or 403s that mention rate limits) get a fixed
60-second backoff with two retries; permission 403s fail immediately.

## Caveats

- **Only the default branch is searched.** This is a hard limit of GitHub's
  code-search API, not a flag this tool could add — "In most cases, this
  will be the master branch," per GitHub's docs. The `branch` column always
  reports that default branch.
- **Only files smaller than 384 KB are searchable** (GitHub's limit).
- Code search covers **indexed** content; a very recent push can take a
  short while to appear in results.
- More than 1,000 matches for one keyword+scope cannot be paged past (a
  GitHub-wide search API limit); the first 1,000 are kept and a warning is
  emitted. Narrow with `--repo` or more specific keywords if you hit this.
- The commit logged for a match is the file's **last-touching commit**, not
  necessarily the commit that introduced the exact matched line — GitHub's
  code-search index doesn't expose per-line blame.
- You only see repositories the `gh` account can see; private-repo results
  require a token/account with access to those repos.
- `commit_message` is the first line only; `committer` is the git-level
  committer name (`commit.committer.name`), not a resolved GitHub login.

## Tests

Fully offline — the gh subprocess seam is faked; no network, no gh binary
needed:

```powershell
py -3 -m unittest discover -s .\tests -v
```
