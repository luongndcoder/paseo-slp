"""Repo config loading plus hard-gate / sandbox-escape scanning for slp-verify."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from slp_git import DiffLine, show
from slp_schema import ROOT, ArtifactError

REPOS_DIR = ROOT / "repos"
MAX_EVIDENCE = 25
SLP_DIR = ".slp/"


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ArtifactError(f"cannot load repo config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ArtifactError(f"{path}: must be a YAML mapping")
    return data


def resolve_config(name: str, explicit: Path | None) -> Path | None:
    """--repo-config wins; otherwise repos/<name>.yaml if it exists."""
    if explicit is not None:
        return explicit
    candidate = REPOS_DIR / f"{name}.yaml"
    return candidate if candidate.is_file() else None


def load_config(name: str, path: Path | None) -> dict[str, Any]:
    """Merge repos/_default.yaml with the repo config (lists are concatenated)."""
    merged = read_yaml(REPOS_DIR / "_default.yaml")
    merged.setdefault("hard_gate_rules", [])
    merged.setdefault("sandbox_escape", {"paths": [], "structured": []})
    merged["commands"] = {}
    if path is None:
        return merged
    repo = read_yaml(path)
    declared = repo.get("repo")
    if declared and declared != name and path.parent.resolve() == REPOS_DIR.resolve():
        raise ArtifactError(f"{path}: declares repo {declared!r} but the worktree repo is {name!r}")
    merged["commands"] = repo.get("commands") or {}
    merged["hard_gate_rules"] = merged["hard_gate_rules"] + (repo.get("hard_gate_rules") or [])
    sb = repo.get("sandbox_escape") or {}
    merged["sandbox_escape"] = {
        "paths": merged["sandbox_escape"].get("paths", []) + (sb.get("paths") or []),
        "structured": merged["sandbox_escape"].get("structured", [])
        + (sb.get("structured") or []),
    }
    return merged


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ArtifactError(f"{where}: bad regex {pattern!r}: {exc}") from exc


def _clip(text: str, n: int = 160) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def _trim(evidence: list[str]) -> list[str]:
    if len(evidence) > MAX_EVIDENCE:
        extra = len(evidence) - MAX_EVIDENCE
        return evidence[:MAX_EVIDENCE] + [f"... and {extra} more match(es)"]
    return evidence


def scan_hard_gates(
    rules: list[dict[str, Any]], files: list[str], lines: list[DiffLine]
) -> list[str]:
    """Return evidence strings; non-empty means at least one hard gate matched."""
    files = [f for f in files if not f.startswith(SLP_DIR)]
    lines = [ln for ln in lines if not ln.path.startswith(SLP_DIR)]
    evidence: list[str] = []
    for rule in rules:
        rid = rule["id"]
        for pat in rule.get("paths") or []:
            rx = _compile(pat, f"rule {rid}")
            evidence += [f"{rid}: path {f} matches /{pat}/" for f in files if rx.search(f)]
        for spec in rule.get("lines") or []:
            rx = _compile(spec["re"], f"rule {rid}")
            sides = spec.get("sides") or ["added", "removed"]
            for ln in lines:
                if ln.side in sides and rx.search(ln.text):
                    sign = "+" if ln.side == "added" else "-"
                    evidence.append(f"{rid}: {ln.path}:{ln.lineno} {sign} {_clip(ln.text)}")
    return _trim(evidence)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, sub in value.items():
            out.update(_flatten(sub, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def _parse_structured(text: str | None, fmt: str, path: str) -> dict[str, Any]:
    if text is None:
        return {}
    try:
        data = json.loads(text) if fmt == "json" else tomllib.loads(text)
    except (ValueError, tomllib.TOMLDecodeError):
        return {"<unparsable>": text}  # unparsable on one side counts as changed
    return _flatten(data)


def scan_sandbox_escape(
    cfg: dict[str, Any], worktree: Path, files: list[str], base: str
) -> list[str]:
    """Paths that touch agent/CI config or hooks, plus structured script/hook key changes."""
    sb = cfg.get("sandbox_escape") or {}
    files = [f for f in files if not f.startswith(SLP_DIR)]
    evidence: list[str] = []
    for pat in sb.get("paths") or []:
        rx = _compile(pat, "sandbox_escape.paths")
        evidence += [f"path {f} matches /{pat}/" for f in files if rx.search(f)]
    for spec in sb.get("structured") or []:
        path_rx = _compile(spec["path_re"], "sandbox_escape.structured")
        key_rxs = [_compile(p, "sandbox_escape.structured") for p in spec["key_patterns"]]
        for f in files:
            if not path_rx.search(f):
                continue
            head = (worktree / f).read_text(errors="replace") if (worktree / f).is_file() else None
            old = _parse_structured(show(worktree, base, f), spec["format"], f)
            new = _parse_structured(head, spec["format"], f)
            for key in sorted(set(old) | set(new)):
                if old.get(key) != new.get(key) and any(r.search(key) for r in key_rxs):
                    evidence.append(f"{f}: key `{key}` changed (script/hook surface)")
    return _trim(evidence)
