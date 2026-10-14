"""Resolve the Paseo CLI (and its client dist dir) used by slp-* tools.

Order: env PASEO_CLI; else the fork CLI if its dist/index.js exists; else the homebrew CLI (with a warning:
it may be older than the daemon and reject the current config).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_FORK_CANDIDATES = [
    _WORKSPACE_ROOT / "paseo" / "packages" / "cli" / "bin" / "paseo",
    *sorted(_WORKSPACE_ROOT.glob("paseo*/packages/cli/bin/paseo")),
]
FORK_CLI = next(
    (c for c in _FORK_CANDIDATES if (c.parent.parent / "dist" / "index.js").is_file()),
    _FORK_CANDIDATES[0],
)
HOMEBREW_CLI = Path("/opt/homebrew/bin/paseo")
HOMEBREW_DIST = Path("/opt/homebrew/lib/node_modules/@getpaseo/cli/dist")
_warned = False


def _fork_ok(cli: Path = FORK_CLI) -> bool:
    return (cli.parent.parent / "dist" / "index.js").is_file()


def resolve_cli() -> str:
    global _warned
    env = os.environ.get("PASEO_CLI")
    if env:
        return env
    if _fork_ok():
        return str(FORK_CLI)
    if not _warned:
        _warned = True
        print(
            f"warning: fork Paseo CLI not found; falling back to {HOMEBREW_CLI}, which may be older than the daemon",
            file=sys.stderr,
        )
    return str(HOMEBREW_CLI)


def resolve_dist() -> str:
    """Client dist dir (`<cli>/dist`) matching resolve_cli(); SLP_PASEO_CLI_DIST overrides."""
    env = os.environ.get("SLP_PASEO_CLI_DIST")
    if env:
        return env
    cli = os.environ.get("PASEO_CLI")
    if cli:
        cand = Path(os.path.realpath(cli)).parent.parent / "dist"
        if (cand / "utils" / "client.js").is_file():
            return str(cand)
    if _fork_ok():
        return str(FORK_CLI.parent.parent / "dist")
    return str(HOMEBREW_DIST)
