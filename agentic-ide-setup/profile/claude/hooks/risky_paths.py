"""The one list of risky paths: a change here needs an independent review.

The closeout hook reads it, and the merge-steward and git-hygiene agent
definitions cite this file instead of keeping their own copies.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath

# Matched against "/" + the repository-relative POSIX path, case-insensitively.
RISKY_PATTERNS = (
    "/azure-pipelines/*", "*/azure-pipelines*.yml", "*/azure-pipelines*.yaml",
    "/scripts/workflows/*",
    "*/migrations/*", "/deploy/sql/*",
    "*/auth/*", "*/authorization/*", "*/identity/*", "*rbac*",
    "/tasks/common/*content_freshness*",
    "/bicep/*", "*.bicep", "/entra/*",
    "/validation/*",
)

# Repositories where every change is a shared-contract change.
RISKY_REPOSITORIES = frozenset({"asset-allocation-contracts"})


def is_risky(relative_path: str, repository: str = "") -> bool:
    if repository.lower() in RISKY_REPOSITORIES:
        return True
    candidate = "/" + PurePosixPath(relative_path.replace("\\", "/")).as_posix().lstrip("/").lower()
    return any(fnmatch(candidate, pattern) for pattern in RISKY_PATTERNS)
