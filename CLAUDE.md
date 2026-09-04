# Claude Code Working Agreements

## Verification Before Claims

Before stating any claim about the codebase, system behavior, or task outcomes as fact, verify it against a direct source — current file contents, command output, test results, logs, or direct observation. Don't rely on memory of earlier context, assumptions about how code "probably" works, or pattern-matching from similar codebases.

This applies especially to:
- Whether a file, function, or config exists and what it currently contains
- Whether tests pass, build succeeds, or a bug is fixed
- Claims about what a teammate's PR or commit does
- Anything that will inform a commit message, PR description, or comment to teammates

Use judgment on low-stakes claims (e.g., "Python lists are zero-indexed") — verification is for things that are wrong often enough to matter and specific enough to check.

If you can't verify something, say so explicitly — flag it as an assumption or unverified, rather than stating it as confirmed. This matters more on a shared codebase: a wrong claim stated as fact can mislead teammates who don't have the context to double-check it.
