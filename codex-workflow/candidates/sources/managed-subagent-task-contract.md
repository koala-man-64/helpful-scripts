# Managed subagent task contract candidate

Managed subagent messages begin with `<codex_subagent_task_v2>` followed by one JSON
object and a closing tag. The object contains exactly these required fields:
`tier`, `objective`, `scope`, `acceptance_checks`, `constraints`,
`decomposition_attempted`, and `delegation`. `dependencies_resolved` is optional and
defaults to true.

`objective` is one bounded non-empty string. `scope`, `acceptance_checks`, and
`constraints` are non-empty bounded string lists. `decomposition_attempted` is true.
`delegation` has exactly `owner_tier`, `role`, `child_count`, and `child_index`.
Choose a permitted direct route, with a child strictly below its owner in model and
effort. Do not omit acceptance checks, invert a denial, or reorder this contract so
the tag is no longer the first non-whitespace message content.

Each task states role-appropriate changes, verification, risks, and blockers only
when applicable. Omit empty sections rather than filling them with placeholders.
