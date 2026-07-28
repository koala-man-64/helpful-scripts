"""Explicit, repository-bound delivery verification outside hook event handling."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit

from .evidence import EvidenceLedger
from .models import DeliveryState, RepoContext
from .utils import run_process, sha256_text


_SUCCESSFUL_POLICY_STATUSES = {
    "approved",
    "notapplicable",
    "not_applicable",
    "succeeded",
}
_MAX_VERIFICATION_BODY_BYTES = 2_000_000


def verify_azure_pull_request(
    ledger: EvidenceLedger,
    *,
    session_id: str,
    context: RepoContext,
    policy: dict[str, Any],
    state: DeliveryState,
    pull_request_id: int,
) -> dict[str, Any]:
    if state not in {
        DeliveryState.PR_OPEN,
        DeliveryState.PR_POLICY_READY,
        DeliveryState.MERGED,
    }:
        raise ValueError("Azure PR verification supports pr_open, pr_policy_ready, or merged.")
    binding = _delivery_binding(ledger, session_id, context, policy)
    if not binding["valid"]:
        return _failed(state, str(binding["error"]))
    azure = binding["azure"]
    code, stdout, stderr = run_process(
        (
            "az",
            "repos",
            "pr",
            "show",
            "--id",
            str(pull_request_id),
            "--organization",
            str(azure["organization"]),
            "--project",
            str(azure["project"]),
            "--output",
            "json",
        ),
        timeout=30,
    )
    if code != 0:
        return _failed(state, stderr or f"provider_exit_{code}")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _failed(state, "invalid_json")
    if not isinstance(payload, dict):
        return _failed(state, "invalid_provider_shape")

    repository = payload.get("repository")
    provider_repository_id = (
        str(repository.get("id", "")).casefold() if isinstance(repository, dict) else ""
    )
    expected_repository_id = str(azure["repository_id"]).casefold()
    source_ref = str(payload.get("sourceRefName", ""))
    target_ref = str(payload.get("targetRefName", ""))
    source_commit = _nested_commit(payload, "lastMergeSourceCommit")
    expected_branch = str(binding["branch"])
    expected_commit = str(binding["commit"])
    target_branch = target_ref.removeprefix("refs/heads/")
    protected = policy.get("protected_branches", [])
    target_is_protected = isinstance(protected, list) and any(
        fnmatchcase(target_branch.casefold(), str(pattern).casefold()) for pattern in protected
    )
    mismatches = []
    if provider_repository_id != expected_repository_id:
        mismatches.append("repository")
    if source_ref != f"refs/heads/{expected_branch}":
        mismatches.append("source_branch")
    if source_commit.casefold() != expected_commit.casefold():
        mismatches.append("source_commit")
    if not target_is_protected:
        mismatches.append("protected_target")
    if mismatches:
        return _failed(state, "binding_mismatch:" + ",".join(mismatches))

    status = str(payload.get("status", "")).casefold()
    if state in {DeliveryState.PR_OPEN, DeliveryState.PR_POLICY_READY}:
        valid_status = status == "active"
    else:
        valid_status = status == "completed"
    if not valid_status:
        return _failed(state, f"unexpected_pr_status:{status or 'missing'}")

    policy_count = 0
    if state is DeliveryState.PR_POLICY_READY:
        policy_result = _verify_pr_policies(
            pull_request_id=pull_request_id,
            organization=str(azure["organization"]),
            project=str(azure["project"]),
        )
        if not policy_result["verified"]:
            return {
                **_failed(state, str(policy_result["error"])),
                "pull_request_id": pull_request_id,
            }
        policy_count = int(policy_result["blocking_policy_count"])

    digest = (
        _nested_commit(payload, "lastMergeCommit")
        if state is DeliveryState.MERGED
        else expected_commit
    )
    ledger.mark_state(
        session_id,
        context,
        state,
        evidence_key=f"azure-pr-{pull_request_id}-{state.value}-{status}",
        source="azure_devops_readback",
        digest=digest,
        metadata={
            "pull_request_id": pull_request_id,
            "status": status,
            "source_branch_hash": sha256_text(source_ref),
            "target_branch_hash": sha256_text(target_ref),
            "blocking_policy_count": policy_count,
        },
    )
    return {
        "verified": True,
        "provider": "azure_devops",
        "pull_request_id": pull_request_id,
        "status": status,
        "state": state.value,
        "repository_bound": True,
        "source_commit": expected_commit,
        "blocking_policy_count": policy_count,
    }


def verify_https_endpoint(
    ledger: EvidenceLedger,
    *,
    session_id: str,
    context: RepoContext,
    policy: dict[str, Any],
    state: DeliveryState,
    url: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    if state not in {DeliveryState.RUNTIME_HEALTHY, DeliveryState.USER_PATH_VERIFIED}:
        raise ValueError("Endpoint verification supports runtime_healthy or user_path_verified.")
    binding = _delivery_binding(ledger, session_id, context, policy)
    if not binding["valid"]:
        return _failed(state, str(binding["error"]))
    target = _https_target(policy, state, url)
    if target is None:
        return _failed(state, "target_not_allowlisted_for_state")
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Verification URL must be HTTPS without credentials or a fragment.")

    expected_commit = str(binding["commit"])
    commit_header = str(target["commit_header"])
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "codex-workflow-hooks/0.1"},
    )
    body = b""
    try:
        with urllib.request.urlopen(  # noqa: S310 - HTTPS and policy allowlist enforced.
            request,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as response:
            status = int(response.status)
            content_type = str(response.headers.get("Content-Type", ""))[:100]
            deployed_commit = str(response.headers.get(commit_header, ""))
            if target.get("body_sha256"):
                body = response.read(_MAX_VERIFICATION_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        content_type = str(exc.headers.get("Content-Type", ""))[:100]
        deployed_commit = str(exc.headers.get(commit_header, ""))
    except (OSError, urllib.error.URLError) as exc:
        return _failed(state, exc.__class__.__name__)

    expected_status = int(target["expected_status"])
    valid = status == expected_status
    errors: list[str] = []
    if not valid:
        errors.append("status")
    expected_content_type = str(target.get("content_type_prefix", ""))
    if expected_content_type and not content_type.casefold().startswith(
        expected_content_type.casefold()
    ):
        errors.append("content_type")
    if deployed_commit.casefold() != expected_commit.casefold():
        errors.append("deployment_commit")
    expected_body_hash = str(target.get("body_sha256", "")).casefold()
    if state is DeliveryState.USER_PATH_VERIFIED:
        if not expected_body_hash:
            errors.append("body_attestation_missing")
        elif len(body) > _MAX_VERIFICATION_BODY_BYTES:
            errors.append("body_too_large")
        elif hashlib.sha256(body).hexdigest().casefold() != expected_body_hash:
            errors.append("body_digest")
    if errors:
        return {
            **_failed(state, "verification_mismatch:" + ",".join(errors)),
            "status": status,
            "expected_status": expected_status,
        }

    target_hash = sha256_text(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
    ledger.mark_state(
        session_id,
        context,
        state,
        evidence_key=f"https-{target_hash}-{state.value}-{status}",
        source="allowlisted_https_readback",
        digest=expected_commit,
        metadata={
            "target_id": str(target["id"]),
            "status": status,
            "content_type": content_type,
            "target_hash": target_hash,
        },
    )
    return {
        "verified": True,
        "status": status,
        "expected_status": expected_status,
        "state": state.value,
        "repository_bound": True,
        "deployment_commit": expected_commit,
        "target_id": str(target["id"]),
    }


def _delivery_binding(
    ledger: EvidenceLedger,
    session_id: str,
    context: RepoContext,
    policy: dict[str, Any],
) -> dict[str, Any]:
    repository = policy.get("repository")
    if not context.managed or not isinstance(repository, dict):
        return {"valid": False, "error": "managed_repository_required"}
    azure = repository.get("azure")
    if not isinstance(azure, dict):
        return {"valid": False, "error": "azure_policy_missing"}
    required_azure = {"organization", "project", "repository_id"}
    if any(not azure.get(key) for key in required_azure):
        return {"valid": False, "error": "azure_policy_incomplete"}
    pushed = ledger.latest_artifact(session_id, context, DeliveryState.PUSHED)
    if not pushed:
        return {"valid": False, "error": "pushed_evidence_missing"}
    commit = str(pushed.get("digest", ""))
    metadata = pushed.get("metadata", {})
    branch = str(metadata.get("branch", "")) if isinstance(metadata, dict) else ""
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        return {"valid": False, "error": "pushed_commit_invalid"}
    if not branch or branch != context.branch or commit.casefold() != context.head.casefold():
        return {"valid": False, "error": "local_binding_changed"}
    return {
        "valid": True,
        "azure": azure,
        "branch": branch,
        "commit": commit,
    }


def _verify_pr_policies(
    *,
    pull_request_id: int,
    organization: str,
    project: str,
) -> dict[str, Any]:
    code, stdout, stderr = run_process(
        (
            "az",
            "repos",
            "pr",
            "policy",
            "list",
            "--id",
            str(pull_request_id),
            "--organization",
            organization,
            "--project",
            project,
            "--output",
            "json",
        ),
        timeout=30,
    )
    if code != 0:
        return {"verified": False, "error": stderr or f"provider_exit_{code}"}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {"verified": False, "error": "invalid_policy_json"}
    if not isinstance(payload, list):
        return {"verified": False, "error": "invalid_policy_shape"}
    blocking = [
        item for item in payload if isinstance(item, dict) and bool(item.get("isBlocking", False))
    ]
    failing = [
        str(item.get("status", "")).casefold()
        for item in blocking
        if str(item.get("status", "")).casefold() not in _SUCCESSFUL_POLICY_STATUSES
    ]
    if not blocking:
        return {"verified": False, "error": "no_blocking_policies"}
    if failing:
        return {
            "verified": False,
            "error": "blocking_policies_not_ready:" + ",".join(sorted(set(failing))),
        }
    return {
        "verified": True,
        "blocking_policy_count": len(blocking),
    }


def _https_target(
    policy: dict[str, Any],
    state: DeliveryState,
    url: str,
) -> dict[str, Any] | None:
    repository = policy.get("repository")
    if not isinstance(repository, dict):
        return None
    verification = repository.get("delivery_verification")
    if not isinstance(verification, dict):
        return None
    targets = verification.get("https_targets")
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, dict) or str(target.get("url", "")) != url:
            continue
        states = target.get("states", [])
        if isinstance(states, list) and state.value in states:
            return target
    return None


def _nested_commit(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value.get("commitId", "")) if isinstance(value, dict) else ""


def _failed(state: DeliveryState, error: str) -> dict[str, Any]:
    return {
        "verified": False,
        "state": state.value,
        "error": error[:500],
    }
