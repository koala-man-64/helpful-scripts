---
name: candidate-browser-evidence
description: Use only when rendered or interactive web state materially affects the requested result; do not use for ordinary factual research or operations a connector, API, or CLI can perform.
---

# Candidate Browser evidence

This disabled candidate is based on the user-owned Browser evidence guidance. Apply it
only when visible or interactive behavior determines correctness. Before browser
actions, use the available `mcp__cua_repl` unified browser capability and follow its
entry-point instructions. Do not require an unavailable skill named `browser`.

For a frontend behavior change, complete relevant code validation, then exercise the
exact affected route and workflow. Record the observed state and result. If supported
Browser capability is unavailable or the route cannot be exercised, state the exact
limitation and provide a concrete manual validation path. Do not call the UI path
verified in that case.

Browser access is capability, not authorization. It does not permit external writes,
sensitive actions, or broader scope.
