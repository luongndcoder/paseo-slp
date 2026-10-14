"""Fixture git repos and CLI runners shared by the slp-* tests (nothing touches real repos)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "bin" / "slp-verify"
EVIDENCE = ROOT / "bin" / "slp-evidence"

APP_PY = textwrap.dedent(
    """\
    def find_campaigns(db, merchant_id):
        query = {"merchant_id": merchant_id, "active": True}
        return list(db.campaigns.find(query))


    def helper():
        return 1
    """
)
OK_CONFIG = """\
repo: fixture
commands:
  test: {cmd: "true", timeout: 30}
  lint: {cmd: "true", timeout: 30}
  typecheck: {skip: "fixture"}
"""
RESULT = {
    "slice_id": "s1",
    "status": "done",
    "files_changed": ["src/app.py"],
    "tests": {"cmd": "true", "passed": 1, "failed": 0},
    "deviations": [],
    "questions": [],
    "hard_gates": [],
    "summary": "ok",
}


def sh(cwd: Path, *cmd: str) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def brief_text(
    slice_id: str, files: list[str], group: str | None = None, base: str = "base"
) -> str:
    lines = [
        "---",
        f"slice_id: {slice_id}",
        "goal: fixture goal",
        "files: " + json.dumps(files),
        f"base_ref: {base}",
        "tests_must_pass: []",
        "out_of_scope: []",
    ]
    if group:
        lines.append(f"parallel_group: {group}")
    return "\n".join(lines + ["---", "body", ""])


class RepoCase(unittest.TestCase):
    """Creates a fixture repo with tag `base`; subclasses add commits/changes."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="slp-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")
        for key, val in (("user.name", "t"), ("user.email", "t@example.com")):
            sh(self.repo, "git", "config", key, val)
        write(self.repo, "src/app.py", APP_PY)
        write(self.repo, "src/other.py", "X = 1\n")
        write(self.repo, ".git/info/exclude", ".slp/\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "base")
        sh(self.repo, "git", "tag", "base")
        self.config = write(self.tmp, "cfg/fixture.yaml", OK_CONFIG)  # outside the worktree

    def commit(self, msg: str = "work") -> None:
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", msg)

    def make_slice(
        self, slice_id: str = "s1", files: list[str] | None = None, group: str | None = None,
        result: bool = True,
    ) -> None:  # fmt: skip
        files = files if files is not None else ["src/app.py"]
        write(self.repo, f".slp/slices/{slice_id}/brief.md", brief_text(slice_id, files, group))
        if result:
            res = {**RESULT, "slice_id": slice_id}
            write(self.repo, f".slp/slices/{slice_id}/result.json", json.dumps(res))

    def verify(
        self, slice_id: str = "s1", config: Path | None = None, extra: list[str] | None = None,
        use_config: bool = True,
    ) -> tuple[int, dict[str, Any] | None, str]:  # fmt: skip
        cmd = [sys.executable, str(VERIFY), str(self.repo), slice_id]
        if use_config:
            cmd += ["--repo-config", str(config or self.config)]
        cmd += extra or []
        env = {**os.environ}
        if use_config:
            env["SLP_REPOS_DIR"] = str(Path(self.config).resolve().parent)  # trusted fixture dir
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        out = self.repo / ".slp" / "slices" / slice_id
        files = sorted(out.glob("verify.*.json")) if out.is_dir() else []
        doc = json.loads(files[-1].read_text()) if files else None
        return res.returncode, doc, res.stdout + res.stderr


def check_of(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in doc["checks"] if c["name"] == name)
