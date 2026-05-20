# Gateway Ledger (Local)

This ledger tracks tool usage and delivery orchestration for work items executed by Codex.

## Policy
- MCP-first: attempt to discover MCP tools/resources; if unavailable, fallback to direct local tools with justification.
- Log: intent → tool → outcome → next decision.

## Session Log

### 2026-04-26
- **Work Item:** `bronze-layer-remediation-20260426` runtime-common remediation.
  - **Branch:** `agent/codex/bronze-layer-remediation-20260426/asset-allocation-runtime-common` from `origin/main` at `2d2f0f4913cf6b1b197203f8eea49146f29d4a9a`.
  - **Scope:** provider-client redaction, typed provider failure semantics, bounded retry/backoff, and disabled-provider handling for bronze job consumers.
  - **Contract routing:** local-only; no shared contract schema or public response shape change planned.
  - **Coordination:** upstream package repo for control-plane and jobs; downstream repos must adopt the patched package after validation.
- **Progress:** Added reusable runtime secret redaction helpers; wired logging formatters and provider exceptions to sanitize messages, details, payloads, and chained traceback causes. Implemented Alpha Vantage retry/backoff with jitter, Retry-After support, unavailable classification, and circuit breaker; hardened Massive/Quiver transient retries and Quiver disabled detection; added typed empty-symbol availability failures before Postgres sync.
- **Validation:** `python -m pytest tests/python/test_redaction.py tests/python/test_alpha_vantage_gateway_client.py tests/python/test_massive_gateway_client.py tests/python/test_quiver_gateway_client.py tests/python/test_symbol_availability.py` -> 44 passed. `python -m pytest` -> 112 passed. `python -m ruff check python tests` -> passed.
- **Coordinator validation:** reran focused remediation tests (`44 passed`), full `python -m pytest -q` (`112 passed`), and `git diff --check` (passed with line-ending warnings only).

### 2026-02-03
- **MCP discovery:** `functions.list_mcp_resources` / `functions.list_mcp_resource_templates` returned empty; no MCP tools available → fallback to local tools permitted.
- **Fallback tooling:** Using `functions.shell_command` and `functions.apply_patch` with explicit intent logging in Orchestrator Updates.
- **Work Item:** `WI-CONFIGJS-001` standardize `/config.js` at domain root (docs + tests + dev proxy toggle).
  - **Code changes:** added `VITE_PROXY_CONFIG_JS` toggle in `ui/vite.config.ts`; documented contract in `docs/config_js_contract.md`; added backend contract tests in `tests/api/test_config_js_contract.py`; updated `.env.template`.
  - **Verification:** `python3 -m pytest -q tests/api/test_config_js_contract.py tests/monitoring/test_system_health.py` → `13 passed`.
