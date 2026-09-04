"""Durable registry of outstanding asynchronous delivery waits.

A wait is recorded when a monitorable operation is observed - a pull request
opened, a pipeline queued - and resolved later by an explicit read-back against
the provider. The registry is the only durable state; everything else is
recomputed from the provider at poll time.

The deliberate design constraint, taken from the codex-workflow-hooks
post-mortem in agentic-ide-setup/docs/codex-wait-scheduling-repair.md:
registration is never gated on a precondition this process maintains itself.
Codex refused to register a monitor unless it could first prove a matching
`pushed` artifact existed in its own ledger; that ledger silently drifted to
empty and the feature never ran once in two weeks. Here the branch and commit
are recorded as bindings to *verify at read-back*, not as preconditions to
check at registration. A wrong binding surfaces as `binding_mismatch` on the
first poll, which is strictly better than never registering at all.
"""

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Matches the Codex timeouts, which were the sound part of that design.
PR_TIMEOUT_HOURS = 72
PIPELINE_TIMEOUT_HOURS = 24
GENERIC_TIMEOUT_MINUTES = 60

TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "timed_out", "human_approval_required", "abandoned"}
)
ACTIVE_STATUSES = frozenset({"registered", "pending"})

PROTECTED_BRANCHES = ("main", "master", "develop", "staging", "production")

# Bounded so a long-lived registry cannot grow without limit.
MAX_RESOLVED_RETAINED = 200
MAX_DIAGNOSTICS_RETAINED = 100


def registry_path() -> Path:
    override = os.environ.get("CLAUDE_WAITS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "waits" / "registry.json"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "waits": [], "diagnostics": []}


def load(path: Path | None = None) -> dict[str, Any]:
    target = path or registry_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return _empty()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt registry must not take the hook down with it. Losing wait
        # rows degrades follow-up; raising here would break every tool call.
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(data.get("waits"), list):
        data["waits"] = []
    if not isinstance(data.get("diagnostics"), list):
        data["diagnostics"] = []
    return data


def _write_atomic(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".registry-",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _prune(data: dict[str, Any]) -> dict[str, Any]:
    waits = [w for w in data.get("waits", []) if isinstance(w, dict)]
    active = [w for w in waits if w.get("status") in ACTIVE_STATUSES]
    resolved = [w for w in waits if w.get("status") not in ACTIVE_STATUSES]
    resolved.sort(key=lambda w: str(w.get("updated_at") or w.get("created_at") or ""))
    data["waits"] = active + resolved[-MAX_RESOLVED_RETAINED:]
    diagnostics = [d for d in data.get("diagnostics", []) if isinstance(d, dict)]
    data["diagnostics"] = diagnostics[-MAX_DIAGNOSTICS_RETAINED:]
    return data


def save(data: dict[str, Any], path: Path | None = None) -> bool:
    """Persist the registry. Never raises.

    Durable state is evidence, not a gate. A read-only disk, a full volume, or a
    transient Windows file lock must not crash the PostToolUse hook and take the
    observed tool call down with it. This mirrors record() in
    pre_tool_use_agent_ladder.py, and matches load(), which already refuses to
    let a corrupt registry break the hook.
    """
    try:
        _write_atomic(_prune(data), path or registry_path())
    except Exception:
        return False
    return True


def _merge_rows(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union by wait_id, keeping whichever row was updated most recently.

    Several Claude sessions share one registry file. Read-modify-write without
    this merge would let a concurrent session's newly registered wait be
    clobbered by whichever process wrote last.

    Recency, not caller preference, decides a collision on one wait_id. A
    poller that has just proved binding_mismatch must not be reverted to a
    stale succeeded by a slower concurrent writer, because poll_one refuses to
    re-examine a terminal status and the wrong answer would then be permanent.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in [*existing, *incoming]:
        if not isinstance(row, dict) or not row.get("wait_id"):
            continue
        key = str(row["wait_id"])
        current = merged.get(key)
        if current is None or _wins(row, current):
            merged[key] = row
    return _dedupe_resources(list(merged.values()))


def _wins(candidate: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    """Whether candidate should replace incumbent for the same wait_id.

    Recency decides, with one override: a merge never silently upgrades a wait
    to `succeeded`. When two terminal statuses disagree, the non-succeeded one
    wins, because `succeeded` is the permissive verdict and poll_one refuses to
    re-examine a terminal wait, so a wrong `succeeded` would be permanent. A
    legitimate success cannot be lost this way: it can only be reached from an
    active status, which never suppresses it here.
    """
    a, b = str(candidate.get("status", "")), str(incumbent.get("status", ""))
    if a != b and a in TERMINAL_STATUSES and b in TERMINAL_STATUSES:
        if a == "succeeded":
            return False
        if b == "succeeded":
            return True
    return _revision(candidate) >= _revision(incumbent)


def _revision(row: dict[str, Any]) -> datetime:
    stamp = parse_time(row.get("updated_at")) or parse_time(row.get("created_at"))
    return stamp or datetime.min.replace(tzinfo=UTC)


def _dedupe_resources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse active rows that describe the same provider resource.

    register() checks for an existing active row before appending, but two
    processes can both read "no active row" before either writes. Collapsing on
    merge makes the idempotency guarantee hold under that interleaving; the
    earliest row wins so the wait_id already reported to a session stays valid.
    """
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") not in ACTIVE_STATUSES:
            kept.append(row)
            continue
        key = (
            str(row.get("provider", "")),
            str(row.get("operation_kind", "")),
            str(row.get("resource_id", "")),
        )
        winner = seen.get(key)
        if winner is None:
            seen[key] = row
            kept.append(row)
            continue
        if str(row.get("created_at", "")) < str(winner.get("created_at", "")):
            kept[kept.index(winner)] = row
            seen[key] = row
    return kept


def _mutate(mutator, path: Path | None = None) -> Any:
    """Re-read, apply, and write, so concurrent sessions do not lose rows."""
    target = path or registry_path()
    data = load(target)
    before = [dict(row) for row in data.get("waits", []) if isinstance(row, dict)]
    result = mutator(data)
    fresh = load(target)
    data["waits"] = _merge_rows(
        [row for row in fresh.get("waits", []) if isinstance(row, dict)],
        _merge_rows(before, [r for r in data.get("waits", []) if isinstance(r, dict)]),
    )
    diagnostics = [d for d in fresh.get("diagnostics", []) if isinstance(d, dict)]
    for entry in data.get("diagnostics", []):
        if entry not in diagnostics:
            diagnostics.append(entry)
    data["diagnostics"] = diagnostics
    save(data, target)
    return result


def timeout_for(operation_kind: str) -> timedelta:
    if operation_kind == "pull_request":
        return timedelta(hours=PR_TIMEOUT_HOURS)
    if operation_kind in {"ci", "release", "deployment", "pipeline"}:
        return timedelta(hours=PIPELINE_TIMEOUT_HOURS)
    return timedelta(minutes=GENERIC_TIMEOUT_MINUTES)


def is_expired(wait: dict[str, Any], reference: datetime | None = None) -> bool:
    created = parse_time(wait.get("created_at"))
    if created is None:
        return True
    limit = timeout_for(str(wait.get("operation_kind", "")))
    return (reference or datetime.now(UTC)) >= created + limit


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def register(
    *,
    provider: str,
    operation_kind: str,
    resource_id: str,
    repository: str,
    branch: str,
    commit: str,
    target_state: str,
    session_id: str = "",
    organization: str = "",
    project: str = "",
    repo_slug: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Record a new wait, or return the existing row for the same resource.

    Idempotent on (provider, operation_kind, resource_id) so a replayed hook
    event cannot create duplicate monitors for one operation.
    """
    wait = {
        "wait_id": uuid.uuid4().hex[:24],
        "provider": provider,
        "operation_kind": operation_kind,
        "resource_id": str(resource_id),
        "repository": repository,
        "branch": branch,
        "commit": commit,
        "target_state": target_state,
        "status": "registered",
        "detail_code": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "last_checked_at": "",
        "session_id": session_id,
        "organization": organization,
        "project": project,
        "repo_slug": repo_slug,
    }

    def mutator(data: dict[str, Any]) -> dict[str, Any]:
        for row in data["waits"]:
            same = (
                isinstance(row, dict)
                and row.get("provider") == provider
                and row.get("operation_kind") == operation_kind
                and str(row.get("resource_id")) == str(resource_id)
                and row.get("status") in ACTIVE_STATUSES
            )
            if same:
                return row
        data["waits"].append(wait)
        return wait

    return _mutate(mutator, path)


def update_status(
    wait_id: str,
    *,
    status: str,
    detail_code: str = "",
    checked: bool = True,
    path: Path | None = None,
) -> dict[str, Any] | None:
    def mutator(data: dict[str, Any]) -> dict[str, Any] | None:
        for row in data["waits"]:
            if isinstance(row, dict) and row.get("wait_id") == wait_id:
                row["status"] = status
                row["detail_code"] = detail_code[:200]
                row["updated_at"] = now_iso()
                if checked:
                    row["last_checked_at"] = now_iso()
                return row
        return None

    return _mutate(mutator, path)


def record_diagnostic(
    code: str, detail: str = "", *, path: Path | None = None
) -> dict[str, Any]:
    """Record a signal that a monitorable operation was seen but not bound.

    Codex returned None here and recorded nothing, which is why a two-week
    total outage produced no alert. An unbound trigger is a defect, so it gets
    written down.
    """
    entry = {"code": code, "detail": detail[:300], "recorded_at": now_iso()}

    def mutator(data: dict[str, Any]) -> dict[str, Any]:
        data["diagnostics"].append(entry)
        return entry

    return _mutate(mutator, path)


def active(path: Path | None = None) -> list[dict[str, Any]]:
    rows = [
        row
        for row in load(path).get("waits", [])
        if isinstance(row, dict) and row.get("status") in ACTIVE_STATUSES
    ]
    rows.sort(key=lambda row: str(row.get("created_at", "")))
    return rows


def get(wait_id: str, path: Path | None = None) -> dict[str, Any] | None:
    for row in load(path).get("waits", []):
        if isinstance(row, dict) and row.get("wait_id") == wait_id:
            return row
    return None


def describe(wait: dict[str, Any]) -> str:
    kind = str(wait.get("operation_kind", "operation"))
    resource = str(wait.get("resource_id", "?"))
    repository = str(wait.get("repository") or "repository")
    status = str(wait.get("status", "registered"))
    detail = str(wait.get("detail_code") or "")
    suffix = f" ({detail})" if detail else ""
    return f"{kind} {resource} in {repository}: {status}{suffix}"


def doctor(path: Path | None = None) -> dict[str, Any]:
    """Liveness report.

    Shipped with the detector, not after it. The Codex feature was dead for two
    weeks because a single count of its registry rows was never taken.
    """
    data = load(path)
    waits = [row for row in data.get("waits", []) if isinstance(row, dict)]
    counts: dict[str, int] = {}
    for row in waits:
        key = str(row.get("status", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    week_ago = datetime.now(UTC) - timedelta(days=7)
    recent = [
        row
        for row in waits
        if (parsed := parse_time(row.get("created_at"))) and parsed >= week_ago
    ]
    overdue = [
        row
        for row in waits
        if row.get("status") in ACTIVE_STATUSES and is_expired(row)
    ]
    diagnostics = [d for d in data.get("diagnostics", []) if isinstance(d, dict)]
    recent_unbound = [
        d
        for d in diagnostics
        if (parsed := parse_time(d.get("recorded_at"))) and parsed >= week_ago
    ]
    problems = []
    if overdue:
        problems.append(f"{len(overdue)} wait(s) past timeout and still active")
    if recent_unbound:
        problems.append(
            f"{len(recent_unbound)} monitorable operation(s) detected in the last 7 days "
            "without a bound wait"
        )
    return {
        "registry": str(path or registry_path()),
        "total": len(waits),
        "by_status": counts,
        "registered_last_7_days": len(recent),
        "overdue": [row.get("wait_id") for row in overdue],
        "unbound_last_7_days": len(recent_unbound),
        "healthy": not problems,
        "problems": problems,
    }
