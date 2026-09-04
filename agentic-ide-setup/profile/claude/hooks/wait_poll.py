"""Read-back polling for registered waits, with binding verification.

Every poll re-reads the live provider resource and verifies that it is still
the thing the wait was registered for before believing any status. A pull
request that was retargeted, or a branch that was force-pushed underneath the
wait, resolves to `binding_mismatch` rather than a false success.

This is the part of the Codex design that was correct and had simply never
executed against a live resource. Verification belongs here, at read-back,
where the answer comes from the provider - not at registration time against a
cache this process maintains itself.

Usage:
    py wait_poll.py poll <wait_id>
    py wait_poll.py poll --all
    py wait_poll.py list
    py wait_poll.py doctor
"""

import json
import shutil
import subprocess
import sys
from typing import Any

import wait_registry

TIMEOUT_SECONDS = 45
SUCCESSFUL_POLICY_STATUSES = frozenset(
    {"approved", "notapplicable", "not_applicable", "succeeded"}
)


def executable(name: str) -> str:
    return shutil.which(name) or shutil.which(f"{name}.cmd") or name


def run(args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def run_json(args: list[str]) -> Any:
    code, output = run(args)
    if code != 0 or not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def az_scope(wait: dict[str, Any]) -> list[str]:
    scope = []
    if wait.get("organization"):
        scope += ["--organization", str(wait["organization"])]
    if wait.get("project"):
        scope += ["--project", str(wait["project"])]
    return scope


def mismatches(checks: list[tuple[str, bool]]) -> list[str]:
    return [name for name, ok in checks if not ok]


def same(left: Any, right: Any) -> bool:
    return str(left or "").casefold() == str(right or "").casefold()


def is_protected(ref: str) -> bool:
    name = str(ref or "").removeprefix("refs/heads/")
    return name in wait_registry.PROTECTED_BRANCHES


def poll_azure_pull_request(wait: dict[str, Any]) -> dict[str, str]:
    payload = run_json(
        [executable("az"), "repos", "pr", "show", "--id", str(wait["resource_id"])]
        + az_scope(wait)
        + ["--output", "json"]
    )
    if not isinstance(payload, dict):
        return {"status": "pending", "detail_code": "provider_unreadable"}

    source = payload.get("lastMergeSourceCommit")
    bad = mismatches(
        [
            (
                "source_branch",
                same(payload.get("sourceRefName"), f"refs/heads/{wait.get('branch')}"),
            ),
            (
                "source_commit",
                not wait.get("commit")
                or (
                    isinstance(source, dict)
                    and same(source.get("commitId"), wait.get("commit"))
                ),
            ),
            ("protected_target", is_protected(str(payload.get("targetRefName", "")))),
        ]
    )
    if bad:
        return {"status": "failed", "detail_code": "binding_mismatch:" + ",".join(bad)}

    status = str(payload.get("status", "")).casefold()
    if status == "completed":
        return {"status": "succeeded", "detail_code": "pr_completed"}
    if status in {"abandoned", "canceled", "cancelled"}:
        return {"status": "abandoned", "detail_code": f"pr_{status}"}
    if status != "active":
        return {"status": "failed", "detail_code": f"unexpected_pr_status:{status}"}

    policies = run_json(
        [executable("az"), "repos", "pr", "policy", "list", "--id", str(wait["resource_id"])]
        + az_scope(wait)
        + ["--output", "json"]
    )
    if not isinstance(policies, list):
        return {"status": "pending", "detail_code": "pr_active"}
    blocking = [p for p in policies if isinstance(p, dict) and p.get("isBlocking")]
    failing = [
        p
        for p in blocking
        if str(p.get("status", "")).casefold() not in SUCCESSFUL_POLICY_STATUSES
    ]
    detail = "policies_ready_awaiting_merge" if blocking and not failing else "policies_pending"
    return {"status": "pending", "detail_code": detail}


def poll_azure_pipeline(wait: dict[str, Any]) -> dict[str, str]:
    payload = run_json(
        [executable("az"), "pipelines", "runs", "show", "--id", str(wait["resource_id"])]
        + az_scope(wait)
        + ["--output", "json"]
    )
    if not isinstance(payload, dict):
        return {"status": "pending", "detail_code": "provider_unreadable"}

    if wait.get("commit") and not same(payload.get("sourceVersion"), wait.get("commit")):
        return {"status": "failed", "detail_code": "binding_mismatch:source_commit"}

    status = str(payload.get("status", "")).casefold()
    result = str(payload.get("result", "")).casefold()
    if status == "completed":
        if result == "succeeded":
            return {"status": "succeeded", "detail_code": "run_succeeded"}
        return {"status": "failed", "detail_code": f"run_{result or 'completed_without_result'}"}
    if status in {"cancelling", "canceled", "cancelled"}:
        return {"status": "abandoned", "detail_code": f"run_{status}"}
    if has_pending_approval(wait):
        # A human owns this gate. Never auto-resume past it.
        return {
            "status": "human_approval_required",
            "detail_code": "approval_user_owned",
        }
    return {"status": "pending", "detail_code": f"run_{status or 'unknown'}"}


def has_pending_approval(wait: dict[str, Any]) -> bool:
    if not wait.get("project"):
        return False
    payload = run_json(
        [
            executable("az"),
            "devops",
            "invoke",
            "--area",
            "pipelineschecks",
            "--resource",
            "approvals",
            "--route-parameters",
            f"project={wait['project']}",
            "--query-parameters",
            "state=pending",
            "--api-version",
            "7.1-preview",
            "--output",
            "json",
        ]
    )
    return approval_matches(payload, str(wait["resource_id"]))


def approval_matches(value: Any, run_id: str) -> bool:
    if isinstance(value, dict):
        pipeline = value.get("pipeline")
        if isinstance(pipeline, dict):
            owner = pipeline.get("owner")
            if isinstance(owner, dict) and str(owner.get("id", "")) == run_id:
                return True
            if str(pipeline.get("id", "")) == run_id:
                return True
        return any(approval_matches(item, run_id) for item in value.values())
    if isinstance(value, list):
        return any(approval_matches(item, run_id) for item in value)
    return False


def poll_github_pull_request(wait: dict[str, Any]) -> dict[str, str]:
    args = [executable("gh"), "pr", "view", str(wait["resource_id"])]
    if wait.get("repo_slug"):
        args += ["--repo", str(wait["repo_slug"])]
    args += ["--json", "state,headRefName,headRefOid,baseRefName,mergedAt"]
    payload = run_json(args)
    if not isinstance(payload, dict):
        return {"status": "pending", "detail_code": "provider_unreadable"}

    bad = mismatches(
        [
            ("source_branch", same(payload.get("headRefName"), wait.get("branch"))),
            (
                "source_commit",
                not wait.get("commit") or same(payload.get("headRefOid"), wait.get("commit")),
            ),
            ("protected_target", is_protected(str(payload.get("baseRefName", "")))),
        ]
    )
    if bad:
        return {"status": "failed", "detail_code": "binding_mismatch:" + ",".join(bad)}

    state = str(payload.get("state", "")).casefold()
    if state == "merged":
        return {"status": "succeeded", "detail_code": "pr_merged"}
    if state == "closed":
        return {"status": "abandoned", "detail_code": "pr_closed"}
    return {"status": "pending", "detail_code": "pr_open"}


POLLERS = {
    ("azure_devops", "pull_request"): poll_azure_pull_request,
    ("azure_devops", "pipeline"): poll_azure_pipeline,
    ("github", "pull_request"): poll_github_pull_request,
}


def poll_one(wait_id: str) -> dict[str, Any]:
    wait = wait_registry.get(wait_id)
    if wait is None:
        return {"wait_id": wait_id, "status": "error", "detail_code": "unknown_wait"}
    if wait.get("status") in wait_registry.TERMINAL_STATUSES:
        return {"wait_id": wait_id, "status": wait["status"], "detail_code": "already_terminal"}
    if wait_registry.is_expired(wait):
        wait_registry.update_status(wait_id, status="timed_out", detail_code="wait_timeout")
        return {"wait_id": wait_id, "status": "timed_out", "detail_code": "wait_timeout"}

    poller = POLLERS.get((str(wait.get("provider")), str(wait.get("operation_kind"))))
    if poller is None:
        wait_registry.update_status(wait_id, status="failed", detail_code="no_poller")
        return {"wait_id": wait_id, "status": "failed", "detail_code": "no_poller"}

    result = poller(wait)
    wait_registry.update_status(
        wait_id, status=result["status"], detail_code=result["detail_code"]
    )
    return {"wait_id": wait_id, **result}


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "list"

    if command == "doctor":
        report = wait_registry.doctor()
        print(json.dumps(report, indent=2))
        return 0 if report["healthy"] else 1

    if command == "list":
        rows = wait_registry.active()
        if not rows:
            print("No outstanding waits.")
            return 0
        for row in rows:
            print(f"{row['wait_id']}  {wait_registry.describe(row)}")
        return 0

    if command == "poll":
        if len(argv) > 1 and argv[1] == "--all":
            rows = wait_registry.active()
            if not rows:
                print("No outstanding waits.")
                return 0
            results = [poll_one(str(row["wait_id"])) for row in rows]
        elif len(argv) > 1:
            results = [poll_one(argv[1])]
        else:
            print("usage: wait_poll.py poll <wait_id> | --all", file=sys.stderr)
            return 2
        print(json.dumps(results, indent=2))
        return 0 if all(r["status"] != "error" for r in results) else 1

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
