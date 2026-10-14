"""Install the slp-* skills for Claude: symlinks in ~/.claude/skills + skillOverrides in ~/.claude/settings.json.

Only names starting with `slp-` are ever created, replaced or removed; other skills and settings keys are untouched.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

PREFIX = "slp-"
# Global user-invocable-only keeps the playbooks out of every non-SLP session; SLP roles switch them on per role.
OVERRIDE_VALUE = "user-invocable-only"
OVERRIDE_NAMES = ("slp-supervisor", "slp-lead")


def source_skills(src_dir: Path) -> dict[str, Path]:
    return {p.name: p for p in sorted(src_dir.glob(f"{PREFIX}*")) if (p / "SKILL.md").is_file()}


def plan_symlinks(src_dir: Path, dst_dir: Path) -> list[tuple[str, str, Path | None]]:
    """Return actions (action, name, target): create | replace | remove | ok | refuse."""
    want = source_skills(src_dir)
    actions: list[tuple[str, str, Path | None]] = []
    for name, target in want.items():
        link = dst_dir / name
        if link.is_symlink():
            same = Path(os.readlink(link)) == target
            actions.append(("ok" if same else "replace", name, target))
        elif link.exists():
            actions.append(("refuse", name, target))  # a real dir/file: never touched
        else:
            actions.append(("create", name, target))
    if dst_dir.is_dir():
        for link in sorted(dst_dir.glob(f"{PREFIX}*")):
            if link.is_symlink() and link.name not in want:  # stale slp-* symlink
                actions.append(("remove", link.name, None))
    return actions


def install_symlinks(src_dir: Path, dst_dir: Path, apply: bool) -> list[str]:
    lines: list[str] = []
    actions = plan_symlinks(src_dir, dst_dir)
    if apply and any(a[0] in {"create", "replace"} for a in actions):
        dst_dir.mkdir(parents=True, exist_ok=True)
    for action, name, target in actions:
        link = dst_dir / name
        if apply:
            if action in {"create", "replace"}:
                if link.is_symlink():
                    link.unlink()
                link.symlink_to(target)  # type: ignore[arg-type]
            elif action == "remove":
                link.unlink()
        lines.append(f"skill symlink {action}: {link}" + (f" -> {target}" if target else ""))
    return lines


def merged_settings(settings_path: Path) -> tuple[str, str]:
    """(current text, new text); new = current with ONLY the two skillOverrides set."""
    current = settings_path.read_text()
    data = json.loads(current)
    overrides = data.setdefault("skillOverrides", {})
    for name in OVERRIDE_NAMES:
        overrides[name] = OVERRIDE_VALUE
    return current, json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def ensure_overrides(settings_path: Path, apply: bool) -> tuple[str, bool]:
    """Returns (unified diff, changed). Re-reads the file right before writing."""
    current, new = merged_settings(settings_path)
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(True), new.splitlines(True), "settings.json (current)", "settings.json (new)", n=1
        )
    )
    # Never rewrite the file when the parsed content is equal (avoids pure re-serialization churn).
    changed = json.loads(current) != json.loads(new)
    if apply and changed:
        current, new = merged_settings(settings_path)
        settings_path.write_text(new)
    return diff, changed
