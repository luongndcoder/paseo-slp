"""hooks/slp-guard.py enforcement logic (SLP_GUARD_ENFORCE=1): Bash allowlists / denies and .slp-only writes,
driven by the locked-policy fixtures; plus the default always-allow behaviour."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

sys.path.insert(0, str(ROOT / "tests"))
from helpers import locked_guard_policy, locked_guard_tree  # noqa: E402

spec = importlib.util.spec_from_file_location("slp_guard", ROOT / "hooks" / "slp-guard.py")
guard = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(guard)  # type: ignore[union-attr]

VERIFY = str(ROOT / "bin" / "slp-verify")
EVIDENCE = str(ROOT / "bin" / "slp-evidence")
MERGE = str(ROOT / "bin" / "slp-merge")


def policy_of(role: str) -> dict:
    """Pre-2026-11-18 locked policies (tests/fixtures/locked_policies): the guard only enforces under SLP_GUARD_ENFORCE=1."""
    return locked_guard_policy(role)


def sh(cwd: Path, *cmd: str) -> None:
    subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)


def init_repo(path: Path, branch: str) -> None:
    path.mkdir(parents=True)
    sh(path, "git", "init", "-q", "-b", branch)
    sh(path, "git", "config", "user.name", "t")
    sh(path, "git", "config", "user.email", "t@example.com")
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("x = 1\n")
    sh(path, "git", "add", "-A")
    sh(path, "git", "commit", "-q", "-m", "base")


class GuardCase(unittest.TestCase):
    """main repo (HEAD on protected `develop`) + worktrees wt-a / wt-b + an unrelated repo `other`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="slp-guard-")
        cls.tmp = Path(cls._tmp.name).resolve()
        cls.main = cls.tmp / "main"
        init_repo(cls.main, "develop")
        cls.wt_a = cls.tmp / "wt-a"
        cls.wt_b = cls.tmp / "wt-b"
        sh(cls.main, "git", "worktree", "add", "-q", "-b", "slp/a", str(cls.wt_a))
        sh(cls.main, "git", "worktree", "add", "-q", "-b", "slp/b", str(cls.wt_b))
        cls.other = cls.tmp / "other"
        init_repo(cls.other, "develop")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.wt_a)})
        env.start()
        self.addCleanup(env.stop)

    # ------------------------------------------------------------ helpers
    def bash_ok(self, role: str, cmd: str, cwd: Path | None = None) -> None:
        try:
            guard.check_bash(cmd, policy_of(role), str(cwd or self.wt_a))
        except guard.Block as exc:
            self.fail(f"{role}: expected ALLOW for {cmd!r}, blocked: {exc}")

    def bash_no(self, role: str, cmd: str, cwd: Path | None = None) -> None:
        with self.assertRaises(guard.Block, msg=f"{role}: expected BLOCK for {cmd!r}"):
            guard.check_bash(cmd, policy_of(role), str(cwd or self.wt_a))

    def write_ok(self, role: str, path: str | Path, cwd: Path | None = None, tool: str = "Write") -> None:
        try:
            guard.check_write(tool, {"file_path": str(path)}, policy_of(role), str(cwd or self.wt_a))
        except guard.Block as exc:
            self.fail(f"{role}: expected write ALLOW for {path}, blocked: {exc}")

    def write_no(self, role: str, path: str | Path, cwd: Path | None = None, tool: str = "Write") -> None:
        with self.assertRaises(guard.Block, msg=f"{role}: expected write BLOCK for {path}"):
            guard.check_write(tool, {"file_path": str(path)}, policy_of(role), str(cwd or self.wt_a))


class LeadBashTests(GuardCase):
    def test_allowed_commands(self) -> None:
        for cmd in (
            f"{VERIFY} {self.wt_a} s1",
            f"{VERIFY} {self.wt_a} s1 --round 2",
            f"{VERIFY} --init {self.wt_a}",
            f"{EVIDENCE} {self.wt_a}/.slp/slices/s1/review.1.json {self.wt_a}",
            f"{EVIDENCE} {self.wt_a}/.slp/final-review.json {self.wt_a} --base test",
            "slp-verify --init /x",
            "slp-merge /x --into master",
            f"{MERGE} {self.wt_a} --into master",
            f"{MERGE} /x --into develop --push",
            f"{MERGE} {self.wt_a} --into test --push",
            "git status",
            "git -C /x log --oneline",
            "git diff HEAD~1",
            "git rev-parse --show-toplevel",
            "git worktree list",
            "git branch --show-current",
            "git show HEAD",
            f"{VERIFY} {self.wt_a} s1 2>&1",
        ):
            with self.subTest(cmd=cmd):
                self.bash_ok("lead", cmd)

    def test_env_prefix_and_wrappers_rejected(self) -> None:
        for cmd in (
            f"SLP_REPOS_DIR=/tmp/x {VERIFY} {self.wt_a} s1",
            f"FOO=1 {EVIDENCE} r.json {self.wt_a}",
            f"env SLP_REPOS_DIR=/tmp/x {VERIFY} {self.wt_a} s1",
            f"sudo {VERIFY} {self.wt_a} s1",
            f"nohup {VERIFY} {self.wt_a} s1",
            f"command {VERIFY} {self.wt_a} s1",
            f"SLP_X=1 {MERGE} {self.wt_a} --into master",
            "SLP_X=1 slp-merge /x --into master",
            f"env GIT_DIR=/x {MERGE} {self.wt_a} --into master",
        ):
            with self.subTest(cmd=cmd):
                self.bash_no("lead", cmd)

    def test_chaining_and_substitution_rejected(self) -> None:
        for cmd in (
            f"{VERIFY} {self.wt_a} s1; rm -rf /tmp/x",
            f"{VERIFY} {self.wt_a} s1 && touch /tmp/x",
            f"{VERIFY} {self.wt_a} s1 || touch /tmp/x",
            f"{VERIFY} {self.wt_a} s1 | tee /tmp/x",
            f"{VERIFY} {self.wt_a} s1 & touch /tmp/x",
            f"{VERIFY} {self.wt_a} s1\ntouch /tmp/x",
            f"{VERIFY} {self.wt_a} s1 > /tmp/x",
            f"{VERIFY} {self.wt_a} $(touch /tmp/x)",
            f"{VERIFY} {self.wt_a} `touch /tmp/x`",
            "git status; touch /tmp/x",
            f"bash -c '{VERIFY} {self.wt_a} s1'",
        ):
            with self.subTest(cmd=cmd):
                self.bash_no("lead", cmd)

    def test_other_commands_and_paths_rejected(self) -> None:
        for cmd in (
            "touch /tmp/x",
            "cat /etc/passwd",
            "ls",
            "python3 -c 'print(1)'",
            f"/tmp/slp-verify {self.wt_a} s1",
            f"{self.wt_a}/bin/slp-verify {self.wt_a} s1",
            f"{ROOT}/bin/slp-build --apply",
            f"{VERIFY}x {self.wt_a} s1",
            "git push origin slp/a",
            "git commit -m x",
            "git checkout test",
            "git branch -D slp/a",
            "git branch -m old new",
            f"{VERIFY} {self.wt_a} s1 --output=/tmp/x",
            "git merge master",
            f"{MERGE}x {self.wt_a} --into master",
            "git push --force origin master",
            f"git -C {self.main} push",
            "git commit --allow-empty -m merge",
            "git add -A",
            "git add .",
            "git rebase master",
            "git pull",
        ):
            with self.subTest(cmd=cmd):
                self.bash_no("lead", cmd)

    def test_merge_protection_follows_git_dash_c(self) -> None:
        # cwd = main checkout whose HEAD is the protected branch `develop`
        self.bash_no("lead", "git merge slp/b", cwd=self.main)
        # merging INTO the workstream worktree (HEAD slp/a) is fine even when cwd is the protected checkout
        self.bash_ok("lead", f"git -C {self.wt_a} merge slp/b", cwd=self.main)
        self.bash_no("lead", f"git -C {self.main} merge slp/b", cwd=self.wt_a)
        self.bash_no("lead", "git merge develop")
        # ref spellings of protected branches must not slip through (origin/, refs/heads/, ~N)
        for ref in ("origin/master", "refs/heads/develop", "remotes/origin/test", "origin/develop~1", "'master'"):
            self.bash_no("lead", f"git merge {ref}")
            self.bash_no("claude-builder", f"git merge {ref}")
        self.bash_ok("lead", "git merge slp/b")
        self.bash_no("lead", f"git --git-dir={self.main}/.git merge slp/b", cwd=self.wt_a)
        self.bash_no("lead", f"git --work-tree={self.main} merge slp/b", cwd=self.wt_a)
        self.bash_ok("lead", "git merge slp/b")

    def test_raw_merge_into_master_stays_blocked(self) -> None:
        main_master = self.tmp / "main-master"
        init_repo(main_master, "master")
        sh(main_master, "git", "branch", "feature")
        self.bash_no("lead", "git merge master", cwd=main_master)
        self.bash_no("lead", "git merge feature", cwd=main_master)
        self.bash_no("lead", f"git -C {main_master} merge feature", cwd=self.wt_a)
        self.bash_ok("lead", f"{MERGE} {self.wt_a} --into master", cwd=main_master)


class LeadWriteTests(GuardCase):
    def test_own_and_sibling_worktree_slp_allowed(self) -> None:
        self.write_ok("lead", self.wt_a / ".slp" / "slices" / "s1" / "brief.md")
        self.write_ok("lead", ".slp/report.md")  # relative to cwd
        self.write_ok("lead", self.wt_b / ".slp" / "slices" / "s2" / "brief.md")
        self.write_ok("lead", self.main / ".slp" / "x.md")  # main checkout is a worktree of the same repo too
        for tool in ("Edit", "MultiEdit"):
            self.write_ok("lead", self.wt_b / ".slp" / "workstream.md", tool=tool)

    def test_sibling_slp_allowed_when_cwd_is_main_checkout(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.main)}):
            self.write_ok("lead", self.wt_b / ".slp" / "workstream.md", cwd=self.main)
            self.write_ok("lead", self.wt_a / ".slp" / "slices" / "s1" / "brief.md", cwd=self.main)
            self.write_no("lead", self.wt_a / "src" / "app.py", cwd=self.main)

    def test_other_repo_slp_blocked(self) -> None:
        self.write_no("lead", self.other / ".slp" / "x.md")
        self.write_no("lead", self.tmp / ".slp" / "x.md")

    def test_source_files_blocked_everywhere(self) -> None:
        self.write_no("lead", self.wt_a / "src" / "app.py")
        self.write_no("lead", self.wt_b / "src" / "app.py")
        self.write_no("lead", self.wt_b / "README.md")
        self.write_no("lead", self.wt_b / ".git" / "info" / "exclude")
        self.write_no("lead", self.wt_b / ".slp")  # the directory itself is not a file under .slp
        self.write_no("lead", self.wt_b / "x.slp" / "y")
        self.write_no("lead", self.wt_b / "src" / ".slp" / "y.md")  # .slp only counts at the worktree root
        self.write_no("lead", "/tmp/slp-evil.txt")
        self.write_no("lead", Path.home() / ".claude" / "settings.json")

    def test_traversal_blocked(self) -> None:
        self.write_no("lead", self.wt_a / ".slp" / ".." / "src" / "app.py")
        self.write_no("lead", ".slp/../src/app.py")
        self.write_no("lead", self.wt_b / ".slp" / ".." / ".." / "other" / ".slp" / "x")
        self.write_no("lead", self.wt_b / ".slp" / ".." / ".." / "wt-a" / "src" / "app.py")
        self.write_no("lead", self.wt_a / ".slp" / ".." / ".." / "main" / "src" / "app.py")

    def test_symlink_escapes_blocked(self) -> None:
        slp_a = self.wt_a / ".slp"
        slp_a.mkdir(exist_ok=True)
        link = slp_a / "to-src"
        if not link.exists():
            link.symlink_to(self.wt_a / "src")
        self.write_no("lead", link / "app.py")
        out = slp_a / "to-other"
        if not out.exists():
            out.symlink_to(self.other)
        self.write_no("lead", out / ".slp" / "x")
        self.write_no("lead", out / "src" / "app.py")
        # a whole `.slp` that is a symlink into another repo / into source
        slp_b = self.wt_b / ".slp"
        if not slp_b.exists():
            slp_b.symlink_to(self.other)
        self.write_no("lead", slp_b / "x.md")
        self.write_no("lead", slp_b / "src" / "app.py")

    def test_notebook_edit_and_empty_path_blocked(self) -> None:
        self.write_no("lead", self.wt_a / ".slp" / "n.ipynb", tool="NotebookEdit")
        with self.assertRaises(guard.Block):
            guard.check_write("Write", {}, policy_of("lead"), str(self.wt_a))


class BuilderBashTests(GuardCase):
    def test_denied(self) -> None:
        for cmd in (
            "git push",
            "git push origin slp/a",
            "git push --force-with-lease origin HEAD",
            "git -C /x push",
            "sudo git push",
            "env FOO=1 git push",
            "FOO=1 git push",
            "bash -c 'git push origin x'",
            "git reset --hard HEAD~1",
            "git reset --hard",
            "git branch -D slp/x",
            "git branch -d slp/x",
            "git branch --delete slp/x",
            "git merge develop",
            "git merge test",
            "git rebase master",
            "git checkout develop",
            "git switch test",
            "git pull origin master",
            "echo $(git push)",
            "rm -rf /",
            "rm -rf ~",
        ):
            with self.subTest(cmd=cmd):
                self.bash_no("claude-builder", cmd)

    def test_merge_while_head_protected_blocked(self) -> None:
        self.bash_no("claude-builder", "git merge slp/b", cwd=self.main)
        self.bash_no("claude-builder", "git pull", cwd=self.main)
        self.bash_ok("claude-builder", "git merge slp/b")  # HEAD slp/a

    def test_normal_work_allowed(self) -> None:
        for cmd in (
            "git add src/app.py",
            "git commit -m 'work'",
            "git status && git diff",
            "pytest tests/ -q",
            "python3 -m unittest",
            "git checkout -b slp/a-fix",
            "git branch --show-current",
            "git log --oneline | head",
            "rm -rf build/",
        ):
            with self.subTest(cmd=cmd):
                self.bash_ok("claude-builder", cmd)

    def test_builder_may_write_anywhere(self) -> None:
        self.write_ok("claude-builder", self.wt_a / "src" / "app.py")


class CheckerRoleTests(GuardCase):
    ROLES = ("claude-checker", "claude-final-reviewer", "claude-integration-reviewer")
    BASH_ROLES = (*ROLES, "claude-advisor")  # the advisor shares the checkers' read-only bash policy

    def test_readonly_bash_allowed(self) -> None:
        for role in self.BASH_ROLES:
            for cmd in (
                "git diff base...HEAD", "git diff", "git log --oneline -5", "git show HEAD:src/app.py",
                "git status", "cat src/app.py", "ls src", "rg merchant_id src", "grep -n x src/app.py",
                "git -C /x diff", "git diff 2>&1",
            ):  # fmt: skip
                with self.subTest(role=role, cmd=cmd):
                    self.bash_ok(role, cmd)

    def test_writes_and_other_commands_blocked(self) -> None:
        for role in self.BASH_ROLES:
            for cmd in (
                "touch x", "rm x", "echo hi", "mkdir d", "cp a b", "mv a b", "sed -i s/a/b/ f", "tee f",
                "python3 -c 'open(1)'", "git push", "git commit -m x", "git checkout .", "git reset --hard",
                "git merge slp/b", "git add .", "git stash", "git apply p.diff", "git config x y",
                "cat a > b", "cat a >> b", "ls > out", "git diff > /tmp/p", "git diff --output=/tmp/p",
                "git diff --ext-diff", "git show --textconv HEAD:a", "rg --pre ./x foo", "rg --pre-glob '*' foo",
                f"{VERIFY} /x s1", f"{EVIDENCE} r.json /x", "cat a; touch b", "cat a && touch b",
                "cat a | tee b", "cat $(touch b)", "bash -c 'touch b'", "sh -c 'touch b'", "env touch b",
                "FOO=1 cat a", "sudo cat a", "cat a\ntouch b",
            ):  # fmt: skip
                with self.subTest(role=role, cmd=cmd):
                    self.bash_no(role, cmd)

    def test_write_only_own_slp(self) -> None:
        for role in self.ROLES:
            with self.subTest(role=role):
                self.write_ok(role, self.wt_a / ".slp" / "slices" / "s1" / "review.1.json")
                self.write_ok(role, self.wt_a / ".slp" / "final-review.json")
                self.write_ok(role, ".slp/integration-review.json")
                self.write_no(role, self.wt_a / "src" / "app.py")
                self.write_no(role, self.wt_a / "README.md")
                self.write_no(role, self.wt_b / ".slp" / "review.1.json")  # sibling worktree: leads only
                self.write_no(role, self.other / ".slp" / "review.1.json")
                self.write_no(role, self.wt_a / ".slp" / ".." / "src" / "app.py")
                self.write_no(role, "/tmp/slp-evil.json")
                self.write_no(role, self.wt_a / ".slp" / "n.ipynb", tool="NotebookEdit")

    def test_checker_symlink_escape_blocked(self) -> None:
        slp = self.wt_a / ".slp"
        slp.mkdir(exist_ok=True)
        link = slp / "esc"
        if not link.exists():
            link.symlink_to(self.wt_a / "src")
        self.write_no("claude-checker", link / "app.py")

    def test_advisor_writes_nothing_anywhere(self) -> None:
        # write: none -> not even its own .slp/ (the advisor answers in chat only)
        for path in (self.wt_a / ".slp" / "x.json", self.wt_a / ".slp" / "slices" / "s1" / "review.1.json",
                     self.wt_a / "src" / "app.py", self.wt_b / ".slp" / "x.json", "/tmp/slp-evil.json"):  # fmt: skip
            with self.subTest(path=str(path)):
                self.write_no("claude-advisor", path)
        self.write_no("claude-advisor", self.wt_a / ".slp" / "n.ipynb", tool="NotebookEdit")


class SupervisorTests(GuardCase):
    def test_no_bash_no_write(self) -> None:
        self.bash_no("supervisor", "ls")
        self.write_no("supervisor", self.wt_a / ".slp" / "x.md")


class MainEntryTests(GuardCase):
    """End-to-end through stdin/exit code. ENFORCE runs use a copied hook tree with the locked policies."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.guard_script = locked_guard_tree(cls.tmp / "guard-tree", ("lead",))

    def run_guard(self, role: str, payload: dict, enforce: bool = True, script: Path | None = None):
        env = {k: v for k, v in os.environ.items() if k != "SLP_GUARD_ENFORCE"}
        env["CLAUDE_PROJECT_DIR"] = str(self.wt_a)
        if enforce:
            env["SLP_GUARD_ENFORCE"] = "1"
        return subprocess.run(
            [sys.executable, str(script or self.guard_script), "--role", role],
            input=json.dumps(payload), capture_output=True, text=True, env=env,
        )  # fmt: skip

    def test_exit_codes_enforced(self) -> None:
        cwd = str(self.wt_a)
        ok = self.run_guard("lead", {"tool_name": "Write", "cwd": cwd, "tool_input": {"file_path": f"{self.wt_b}/.slp/x.md"}})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = self.run_guard("lead", {"tool_name": "Write", "cwd": cwd, "tool_input": {"file_path": f"{self.wt_b}/src/app.py"}})
        self.assertEqual(bad.returncode, 2)
        bash = self.run_guard("lead", {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": "touch /tmp/x"}})
        self.assertEqual(bash.returncode, 2)
        broken = subprocess.run(
            [sys.executable, str(self.guard_script), "--role", "lead"],
            input="not json", capture_output=True, text=True,
            env={**os.environ, "SLP_GUARD_ENFORCE": "1"},
        )  # fmt: skip
        self.assertEqual(broken.returncode, 2)  # fail closed

    def test_default_allows_everything_without_enforce(self) -> None:
        """Since 2026-11-18: no SLP_GUARD_ENFORCE -> always exit 0, using both the locked tree and the live hook."""
        cwd = str(self.wt_a)
        calls = (
            {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": "git push --force origin master"}},
            {"tool_name": "Write", "cwd": cwd, "tool_input": {"file_path": f"{self.wt_b}/src/app.py"}},
            {"tool_name": "Write", "cwd": cwd, "tool_input": {"file_path": "/tmp/slp-anywhere.txt"}},
        )
        for script in (self.guard_script, ROOT / "hooks" / "slp-guard.py"):
            for payload in calls:
                with self.subTest(script=str(script), tool=payload["tool_name"]):
                    res = self.run_guard("lead", payload, enforce=False, script=script)
                    self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
