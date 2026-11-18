"""Subset of a role policy that hooks/slp-guard.py reads (written to build/claude/<role>.policy.json)."""
from __future__ import annotations

from typing import Any


def guard_policy(policy: dict[str, Any]) -> dict[str, Any]:
    out = {
        "role": policy["role"],
        "write": policy["write"],
        "bash": policy["bash"],
        "bash_allow": policy.get("bash_allow", []),
        "bash_deny": policy.get("bash_deny", []),
        "protected_branches": policy.get("protected_branches", []),
        # slp-only writes: "cwd" = .slp/ of the workspace; "repo" = .slp/ of any worktree of the same repo
        "slp_scope": policy.get("slp_scope", "cwd"),
    }
    if "spawn_allow" in policy:
        # provider ids (the part of `provider[/model]` before the first "/") create_agent may use; set only for
        # roles that get the Paseo-MCP spawn hook (update_agent / set_agent_mode are always blocked there)
        out["spawn_allow"] = list(policy["spawn_allow"])
    return out
