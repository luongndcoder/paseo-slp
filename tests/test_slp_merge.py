"""bin/slp-merge: merge (and push) a workstream branch into test/master/develop only for a passing, non-stale final review."""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from helpers import ROOT, write  # noqa: E402
from slp_git_push_block import with_push_block  # noqa: E402

MERGE = ROOT / "bin" / "slp-merge"
_loader = importlib.machinery.SourceFileLoader("slp_merge", str(MERGE))
_spec = importlib.util.spec_from_loader("slp_merge", _loader)
slp_merge = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_loader.exec_module(slp_merge)


def git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


class MergeCase(unittest.TestCase):
    """main checkout on `master` + workstream worktree `ws` on branch slp/ws with one commit and a passing review."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="slp-merge-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.roots = self.tmp / "personal"
        self.main = self.roots / "repo"
        self.main.mkdir(parents=True)
        git(self.main, "init", "-q", "-b", "master")
        git(self.main, "config", "user.name", "t")
        git(self.main, "config", "user.email", "t@example.com")
        write(self.main, "a.txt", "one\n")
        write(self.main, ".git/info/exclude", ".slp/\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "base")
        self.ws = self.tmp / "ws"
        git(self.main, "worktree", "add", "-q", "-b", "slp/ws", str(self.ws))
        write(self.ws, "b.txt", "feature\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "feature")
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "master", str(self.remote))
        git(self.main, "remote", "add", "origin", str(self.remote))
        git(self.main, "push", "-q", "-u", "origin", "master")
        self.policy = {
            "branches": ["test", "master", "develop"],
            "roots": [self.roots.resolve()],
            "deny_roots": [],
            "push": True,
            "protected_branches": ["test", "master", "develop"],
        }
        self.write_review()

    def checkout_target(self, branch: str, upstream: bool = True) -> None:
        """Make `branch` (branched from master) the branch checked out in the main checkout."""
        git(self.main, "checkout", "-q", "-b", branch, "master")
        if upstream:
            git(self.main, "push", "-q", "-u", "origin", branch)

    def remote_ref(self, branch: str) -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{branch}")

    def other_clone_push(self, branch: str, fname: str) -> None:
        """Advance the remote `branch` from a second clone (simulates somebody else pushing)."""
        clone = self.tmp / f"other-{fname}"
        git(self.tmp, "clone", "-q", "-b", branch, str(self.remote), str(clone))
        git(clone, "config", "user.name", "t")
        git(clone, "config", "user.email", "t@example.com")
        write(clone, fname, "x\n")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "elsewhere")
        git(clone, "push", "-q", "origin", branch)

    def write_review(self, verdict: str = "pass", head: str | None = None, checked: bool = True) -> None:
        doc = {
            "slice_id": "final", "round": 1, "verdict": verdict, "findings": [], "base_ref": "master",
            "head_sha": head or git(self.ws, "rev-parse", "HEAD"),
        }  # fmt: skip
        write(self.ws, ".slp/final-review.json", json.dumps(doc))
        if checked:
            write(self.ws, ".slp/final-review.checked.json", json.dumps({**doc, "status": "checked"}))

    def run_merge(
        self, into: str = "master", worktree: Path | None = None, push: bool = False, before_push: dict | None = None,
    ) -> tuple[int, str]:  # fmt: skip
        """Runs main() in-process. Every git push argv is recorded in self.pushes; `before_push` maps a refspec
        suffix to a callback run right before that push (to simulate a race)."""
        out, err = io.StringIO(), io.StringIO()
        self.pushes: list[list[str]] = []
        real_run = subprocess.run

        def spy(argv, *a, **kw):  # type: ignore[no-untyped-def]
            if isinstance(argv, list) and "push" in argv and "-C" in argv:  # slp-merge's own pushes only
                self.pushes.append(argv)
                for suffix, cb in (before_push or {}).items():
                    if argv[-1].endswith(suffix):
                        cb()
            return real_run(argv, *a, **kw)

        args = [str(worktree or self.ws), "--into", into] + (["--push"] if push else [])
        with mock.patch.object(slp_merge, "load_merge_policy", lambda: self.policy), \
                mock.patch.object(subprocess, "run", spy), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):  # fmt: skip
            rc = slp_merge.main(args)
        return rc, out.getvalue() + err.getvalue()

    def assert_refused(self, text: str, **kw: object) -> None:
        rc, out = self.run_merge(**kw)
        self.assertEqual(rc, 1, out)
        self.assertIn(text, out)
        self.assertFalse((self.ws / ".slp" / "merge.json").exists())

    def master_head(self) -> str:
        return git(self.main, "rev-parse", "master")


class MergeTests(MergeCase):
    def test_happy_path_merges_and_writes_merge_json(self) -> None:
        before = self.master_head()
        source_sha = git(self.ws, "rev-parse", "HEAD")
        rc, out = self.run_merge()
        self.assertEqual(rc, 0, out)
        doc = json.loads((self.ws / ".slp" / "merge.json").read_text())
        self.assertEqual(doc["merge_sha"], self.master_head())
        self.assertNotEqual(doc["merge_sha"], before)
        self.assertEqual(doc["into"], "master")
        self.assertEqual(doc["source_branch"], "slp/ws")
        self.assertEqual(doc["source_sha"], source_sha)
        self.assertEqual(doc["final_review_head_sha"], source_sha)
        self.assertEqual(Path(doc["target_worktree"]), self.main)
        self.assertTrue(doc["merged_at"].endswith("+00:00"))
        self.assertEqual(doc["pushed"], {"remote": None, "source": False, "target": False})
        self.assertEqual(self.pushes, [])  # without --push the remote is never touched
        self.assertNotEqual(self.remote_ref("master"), doc["merge_sha"])
        self.assertEqual(git(self.main, "rev-list", "--parents", "-n1", "master").split()[1:], [before, source_sha])
        self.assertIn(f"SLP final review pass {source_sha[:12]}", git(self.main, "log", "-1", "--format=%s"))
        self.assertTrue((self.main / "b.txt").is_file())

    def test_merges_into_test_and_develop(self) -> None:
        for branch in ("test", "develop"):
            with self.subTest(branch=branch):
                self.checkout_target(branch)
                rc, out = self.run_merge(into=branch)
                self.assertEqual(rc, 0, out)
                doc = json.loads((self.ws / ".slp" / "merge.json").read_text())
                self.assertEqual(doc["into"], branch)
                self.assertEqual(doc["merge_sha"], git(self.main, "rev-parse", branch))
                git(self.main, "checkout", "-q", "master")

    def test_refuses_branch_not_in_policy(self) -> None:
        self.policy["branches"] = ["master"]
        git(self.main, "branch", "develop")
        self.assert_refused("not mergeable", into="develop")
        self.assert_refused("not mergeable", into="feature-x")

    def test_roots_empty_allows_any_repo(self) -> None:
        self.policy["roots"] = []
        rc, out = self.run_merge()
        self.assertEqual(rc, 0, out)

    def test_loader_treats_missing_roots_as_every_repo(self) -> None:
        pol = write(self.tmp, "policy.yaml", "protected_branches: [master]\nprotected_merge:\n  branches: [master]\n")
        with mock.patch.object(slp_merge, "POLICY_PATH", pol):
            loaded = slp_merge.load_merge_policy()
        self.assertEqual((loaded["roots"], loaded["deny_roots"], loaded["push"]), ([], [], False))

    def test_refuses_repo_outside_roots(self) -> None:
        self.policy["roots"] = [(self.tmp / "elsewhere").resolve()]
        before = self.master_head()
        self.assert_refused("outside protected_merge.roots")
        self.assertEqual(self.master_head(), before)

    def test_deny_roots_win_over_roots(self) -> None:
        self.policy["deny_roots"] = [self.main.resolve()]
        before = self.master_head()
        self.assert_refused("deny_roots")
        self.assertEqual(self.master_head(), before)

    def test_deny_roots_apply_without_roots(self) -> None:
        self.policy["roots"] = []
        self.policy["deny_roots"] = [self.roots.resolve()]
        self.assert_refused("deny_roots")

    def test_refuses_missing_checked_review(self) -> None:
        (self.ws / ".slp" / "final-review.checked.json").unlink()
        self.assert_refused("final-review.checked.json")

    def test_refuses_verdict_fix(self) -> None:
        self.write_review(verdict="fix")
        self.assert_refused("not 'pass'")

    def test_refuses_stale_review(self) -> None:
        write(self.ws, "c.txt", "later\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "after review")
        self.assert_refused("STALE")

    def test_refuses_stale_status_from_slp_evidence(self) -> None:
        self.write_review()
        path = self.ws / ".slp" / "final-review.checked.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "status": "stale"}))
        self.assert_refused("status is 'stale'")

    def test_refuses_dirty_source(self) -> None:
        write(self.ws, "b.txt", "edited\n")
        self.assert_refused("source worktree has uncommitted")

    def test_refuses_dirty_target(self) -> None:
        write(self.main, "a.txt", "edited\n")
        before = self.master_head()
        self.assert_refused("target worktree")
        self.assertEqual(self.master_head(), before)

    def test_refuses_when_master_not_checked_out(self) -> None:
        git(self.main, "checkout", "-q", "-b", "scratch")
        self.assert_refused("check out master")
        self.assertEqual(git(self.main, "branch", "--show-current"), "scratch")

    def test_refuses_detached_source(self) -> None:
        git(self.ws, "checkout", "-q", "--detach")
        self.assert_refused("detached HEAD")

    def test_conflict_aborts_and_leaves_master_unchanged(self) -> None:
        write(self.ws, "a.txt", "ws side\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "ws edit")
        write(self.main, "a.txt", "master side\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "master edit")
        self.write_review()
        before = self.master_head()
        rc, out = self.run_merge()
        self.assertEqual(rc, 1, out)
        self.assertIn("a.txt", out)
        self.assertEqual(self.master_head(), before)
        self.assertFalse((self.main / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(git(self.main, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertFalse((self.ws / ".slp" / "merge.json").exists())

    def test_already_merged_is_refused(self) -> None:
        self.assertEqual(self.run_merge()[0], 0)
        (self.ws / ".slp" / "merge.json").unlink()
        self.assert_refused("already contained")


class PushTests(MergeCase):
    def test_push_updates_remote_source_and_target(self) -> None:
        source_sha = git(self.ws, "rev-parse", "HEAD")
        rc, out = self.run_merge(push=True)
        self.assertEqual(rc, 0, out)
        doc = json.loads((self.ws / ".slp" / "merge.json").read_text())
        self.assertEqual(doc["pushed"], {"remote": "origin", "source": True, "target": True})
        self.assertEqual(self.remote_ref("slp/ws"), source_sha)
        self.assertEqual(self.remote_ref("master"), doc["merge_sha"])
        self.assertEqual(self.remote_ref("master"), self.master_head())

    def test_push_into_test_and_develop(self) -> None:
        for branch in ("test", "develop"):
            with self.subTest(branch=branch):
                self.checkout_target(branch)
                rc, out = self.run_merge(into=branch, push=True)
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.remote_ref(branch), git(self.main, "rev-parse", branch))
                git(self.main, "checkout", "-q", "master")

    def test_push_argv_never_forces_deletes_or_mirrors(self) -> None:
        rc, out = self.run_merge(push=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.pushes), 2)  # source, then target
        for argv in self.pushes:
            tail = argv[argv.index("push") + 1 :]
            self.assertEqual(len(tail), 2, tail)  # `<remote> <refspec>` only: no flags at all
            self.assertFalse(any(a.startswith("-") or a.startswith("+") for a in tail), tail)
        self.assertTrue(self.pushes[0][-1].endswith(":refs/heads/slp/ws"))
        self.assertEqual(self.pushes[1][-1], "refs/heads/master:refs/heads/master")

    def test_refuses_push_when_policy_disallows(self) -> None:
        self.policy["push"] = False
        self.assert_refused("--push is not enabled", push=True)

    def test_refuses_when_no_upstream(self) -> None:
        git(self.main, "config", "--unset", "branch.master.remote")
        before = self.master_head()
        self.assert_refused("set upstream for master", push=True)
        self.assertEqual(self.master_head(), before)
        self.assertEqual(self.pushes, [])

    def test_refuses_when_local_ahead_of_remote(self) -> None:
        write(self.main, "a.txt", "local only\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "unpushed")
        before = self.master_head()
        self.assert_refused("sync the main checkout", push=True)
        self.assertEqual(self.master_head(), before)
        self.assertEqual(self.pushes, [])

    def test_refuses_when_local_behind_remote(self) -> None:
        self.other_clone_push("master", "elsewhere.txt")
        before = self.master_head()
        self.assert_refused("sync the main checkout", push=True)
        self.assertEqual(self.master_head(), before)
        self.assertEqual(self.pushes, [])

    def test_source_push_rejected_leaves_nothing_merged(self) -> None:
        clone = self.tmp / "other-src"
        git(self.tmp, "clone", "-q", str(self.remote), str(clone))
        git(clone, "config", "user.name", "t")
        git(clone, "config", "user.email", "t@example.com")
        git(clone, "checkout", "-q", "-b", "slp/ws")
        write(clone, "diverge.txt", "x\n")
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", "diverge")
        git(clone, "push", "-q", "origin", "slp/ws")  # remote slp/ws diverges from ours: non-ff
        before = self.master_head()
        self.assert_refused("nothing merged", push=True)
        self.assertEqual(self.master_head(), before)
        self.assertEqual(len(self.pushes), 1)

    def test_target_push_race_keeps_local_merge_and_escalates(self) -> None:
        rc, out = self.run_merge(
            push=True,
            before_push={":refs/heads/master": lambda: self.other_clone_push("master", "race.txt")},
        )
        self.assertEqual(rc, 1, out)
        self.assertIn("ESCALATE", out)
        self.assertIn("LOCALLY", out)
        doc = json.loads((self.ws / ".slp" / "merge.json").read_text())
        self.assertEqual(doc["pushed"], {"remote": "origin", "source": True, "target": False})
        self.assertEqual(doc["merge_sha"], self.master_head())  # kept, not reset
        self.assertNotEqual(self.remote_ref("master"), doc["merge_sha"])
        self.assertFalse((self.main / ".git" / "MERGE_HEAD").exists())
        self.assertFalse(any(a.startswith(("-", "+")) for argv in self.pushes for a in argv[argv.index("push") + 1 :]))


class RealPolicyTests(MergeCase):
    """Subprocess runs against the shipped roles/lead/policy.yaml (no env/arg override possible)."""

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(MERGE), *args], capture_output=True, text=True,
                              env=env or {"PATH": os.environ["PATH"], "HOME": str(Path.home())})  # fmt: skip

    def test_real_policy_shape(self) -> None:
        pol = slp_merge.load_merge_policy()
        self.assertEqual(pol["branches"], ["test", "master", "develop"])
        self.assertEqual((pol["roots"], pol["deny_roots"]), ([], []))
        self.assertIs(pol["push"], True)
        self.assertEqual(pol["protected_branches"], [])  # 2026-11-18: guard-level protection removed; protected_merge.branches stays

    def test_real_policy_merges_repo_anywhere(self) -> None:
        res = self.run_cli(str(self.ws), "--into", "master")  # repo lives in a tmp dir, outside ~/Documents/AI
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_real_policy_refuses_unlisted_branch(self) -> None:
        res = self.run_cli(str(self.ws), "--into", "release")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("not mergeable", res.stderr)

    def test_push_works_despite_inherited_push_block_env(self) -> None:
        """The Lead's process carries the pushInsteadOf push-block; slp-merge strips GIT_* so its push succeeds,
        while a raw `git push` with the same env fails."""
        env = with_push_block({"PATH": os.environ["PATH"], "HOME": str(Path.home())})
        raw = subprocess.run(["git", "-C", str(self.main), "push", "origin", "master"], capture_output=True, text=True, env=env)
        self.assertNotEqual(raw.returncode, 0)
        res = self.run_cli(str(self.ws), "--into", "master", "--push", env=env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.remote_ref("master"), self.master_head())

    def test_usage_error_exit_2(self) -> None:
        self.assertEqual(self.run_cli().returncode, 2)


if __name__ == "__main__":
    unittest.main()
