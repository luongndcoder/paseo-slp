"""Subset of a role policy that hooks/slp-guard.py reads (written to build/claude/<role>.policy.json)."""
from __future__ import annotations

from typing import Any


def guard_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": policy["role"],
        "write": policy["write"],
        "bash": policy["bash"],
        "bash_allow": policy.get("bash_allow", []),
        "bash_deny": policy.get("bash_deny", []),
        "protected_branches": policy.get("protected_branches", []),
        # slp-only writes: "cwd" = .slp/ of the workspace; "repo" = .slp/ of any worktree of the same repo
        "slp_scope": policy.get("slp_scope", "cwd"),
    }
