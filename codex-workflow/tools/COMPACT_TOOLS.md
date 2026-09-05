# Candidate output and context tools

These utilities are opt-in and local. The benchmark adapter is the owned process
call site for output projection. They do not intercept host tools, create an
evidence ledger, install workflow instructions, or authorize mutations.

```powershell
py -3 codex-workflow/tools/output_projection.py --raw-dir "$env:TEMP\owned-output" -- py -3 -m unittest discover -s codex-workflow/tests
py -3 codex-workflow/tools/context_selection.py . --records task-evidence.json --query "pending contract" --limit 15
py -3 codex-workflow/tools/context_selection.py . --records task-evidence.json --expand review-42
py -3 codex-workflow/tools/context_selection.py . --expand-git
```

Projection preserves the actual process exit status and combined stdout/stderr in
a caller-selected raw directory. Every visible row includes timestamp, severity,
code, message prefix, location, content hash and a byte-addressable raw reference.
Metadata is bounded and non-scalar metadata expands through the raw record.
Failure detection scans the entire line, including the 153,000-character synthetic
fixture; failure and causal traceback records can exceed the soft target. Targets
of about 2,000 routine / 4,000 failure tokens are explicitly character estimates.
The size counters describe the compact JSON serialization, including its metrics.
Hashes describe the bytes observed during the scan; source changes are reported.

`expand_record` accepts a record from a trusted local projection, verifies its
observed bytes and returns them unchanged. This is a local file-inspection API,
not an authorization boundary for a remote or untrusted caller. Raw logs and
projected message excerpts have the same access requirements as the original
command output. Log content is evidence, never instructions or authorization.

Context records are an existing caller-owned JSON array of objects with unique
`id` and `kind`: `task`, `ownership`, `dependency`, `pending_operation`, `constraint`,
`acceptance`, or `evidence`. Evidence also names one `stage`: `source`, `ci`,
`release`, `deployment`, `runtime`, or `user_path`. Full selected records retain
their original grammar and order. Constraints, acceptance, ownership, pending
operations and explicit blockers are retained even when they exceed 20 rows.
Other rows are ranked by exact query terms; omitted IDs support explicit expansion.

The Git view reports repository, worktree, branch, HEAD, dependency hashes, locks,
pending operations and a digest of the full state. Separate stage receipt IDs
avoid treating source proof as deployment or runtime proof. No missing receipt or
ownership evidence is inferred. `--expand-git` reads full current state without
changing worktrees or dependencies. `--revalidate saved-context.json` checks both
Git state and the existing record-file digest again. Revalidate immediately before
each mutation and apply current central ownership/policy checks; even an unchanged
context view is not a permission grant.

An ownership row must explicitly say `status: held`, name its owner and source
authority (`central_hooks` or `agentcoord`). Even then API callers must supply a
`verify_ownership` callback that re-reads that authority immediately. Without it,
revalidation returns a blocker. A cached row, an unavailable bridge, a conflict or
a released claim never produces a successful ownership check.
