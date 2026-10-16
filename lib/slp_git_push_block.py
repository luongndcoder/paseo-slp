"""Git-level push block for SLP builder providers (provider `env`, no hook involved).

WHY: Claude's guard hook only sees the Bash command line (`git push` can hide behind aliases, scripts,
`git -c`, a Makefile target ...), Pi has no permission layer at all and OpenCode's shared server may ignore
per-provider env. Rewriting EVERY push URL to an unknown transport makes the push fail inside git itself,
whatever launched it, while `git fetch` / `git commit` stay untouched (`pushInsteadOf` only affects pushes).

GIT_CONFIG_COUNT/KEY_i/VALUE_i is git's env-based config injection (git >= 2.31); it beats repo/global config.
"""
from __future__ import annotations

import re

NOPUSH_BASE = "nopush:///"  # no such transport => "fatal: unable to find remote helper for 'nopush'"
KEY = f"url.{NOPUSH_BASE}.pushInsteadOf"
# The prefixes the task requires, plus relative local paths and git:// (a relative `../bare.git` origin or
# a git:// remote would otherwise match none of them).
PREFIXES = ["git@", "https://", "http://", "ssh://", "file://", "/", "./", "../", "git://"]

_KEY_RE = re.compile(r"GIT_CONFIG_(KEY|VALUE)_(\d+)$")


def _existing_pairs(env: dict[str, str]) -> list[tuple[str, str]]:
    """Valid (key, value) pairs already present in env, in index order (GIT_CONFIG_COUNT bounds them)."""
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    pairs = []
    for i in range(max(count, 0)):
        k, v = env.get(f"GIT_CONFIG_KEY_{i}"), env.get(f"GIT_CONFIG_VALUE_{i}")
        if k is not None and v is not None:
            pairs.append((k, v))
    return pairs


def with_push_block(env: dict[str, str] | None) -> dict[str, str]:
    """Return a copy of env with the push-block pairs merged into any existing GIT_CONFIG_* pairs (renumbered)."""
    env = dict(env or {})
    pairs = _existing_pairs(env)
    for key in [k for k in env if k == "GIT_CONFIG_COUNT" or _KEY_RE.match(k)]:
        del env[key]
    ours = [(KEY, p) for p in PREFIXES]
    pairs = [p for p in pairs if p not in ours] + ours  # idempotent: never duplicate our own pairs
    out: dict[str, str] = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (k, v) in enumerate(pairs):
        out[f"GIT_CONFIG_KEY_{i}"] = k
        out[f"GIT_CONFIG_VALUE_{i}"] = v
    return {**env, **out}
