from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import VERIFY, RepoCase, sh, write  # noqa: E402


def init(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(VERIFY), "--init", str(path)], capture_output=True, text=True)


class InitTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        (self.repo / ".git" / "info" / "exclude").write_text("# comment\n*.log")  # no trailing newline

    def lines(self) -> list[str]:
        return (self.repo / ".git" / "info" / "exclude").read_text().splitlines()

    def test_appends_once_and_is_idempotent(self) -> None:
        res = init(self.repo)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("appended", res.stdout)
        self.assertEqual(self.lines(), ["# comment", "*.log", ".slp/"])
        res = init(self.repo)
        self.assertEqual(res.returncode, 0)
        self.assertIn("already excluded", res.stdout)
        self.assertEqual(self.lines().count(".slp/"), 1)

    def test_git_ignores_slp_after_init(self) -> None:
        write(self.repo, ".slp/x.txt", "x")
        self.assertIn(".slp/", sh(self.repo, "git", "status", "--porcelain"))
        init(self.repo)
        self.assertNotIn(".slp", sh(self.repo, "git", "status", "--porcelain"))

    def test_worktree_writes_to_common_dir(self) -> None:
        wt = self.tmp / "wt"
        sh(self.repo, "git", "worktree", "add", "-q", "-b", "feat", str(wt))
        res = init(wt)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.lines().count(".slp/"), 1)
        self.assertIn(str((self.repo / ".git").resolve()), res.stdout)
        write(wt, ".slp/y.txt", "y")
        self.assertNotIn(".slp", sh(wt, "git", "status", "--porcelain"))

    def test_creates_missing_info_dir(self) -> None:
        sh(self.repo, "rm", "-rf", str(self.repo / ".git" / "info"))
        self.assertEqual(init(self.repo).returncode, 0)
        self.assertEqual(self.lines(), [".slp/"])

    def test_bad_input_exit_2(self) -> None:
        self.assertEqual(init(self.tmp / "missing").returncode, 2)
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(init(plain).returncode, 2)  # not a git repo
        res = subprocess.run([sys.executable, str(VERIFY), "--init", str(self.repo), "s1"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)

    def test_slice_id_still_required_without_init(self) -> None:
        res = subprocess.run([sys.executable, str(VERIFY), str(self.repo)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
