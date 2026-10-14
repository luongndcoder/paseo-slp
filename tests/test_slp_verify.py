from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from helpers import RepoCase, check_of, write  # noqa: E402
from slp_schema import schema_errors  # noqa: E402


class VerifyTests(RepoCase):
    def test_clean_in_scope_diff_passes(self) -> None:
        write(self.repo, "src/app.py", (self.repo / "src/app.py").read_text() + "\n# note\n")
        self.make_slice()
        code, doc, out = self.verify()
        self.assertEqual(code, 0, out)
        assert doc is not None
        self.assertEqual(doc["verdict"], "pass")
        self.assertEqual(check_of(doc, "test")["status"], "pass")
        self.assertEqual(check_of(doc, "typecheck")["status"], "skipped")
        self.assertEqual(check_of(doc, "scope")["status"], "pass")
        self.assertEqual(check_of(doc, "contract")["status"], "pass")
        self.assertEqual(schema_errors(doc, "verify"), [])

    def test_committed_and_untracked_changes_are_both_seen(self) -> None:
        write(self.repo, "src/app.py", (self.repo / "src/app.py").read_text() + "\n# c\n")
        self.commit()
        write(self.repo, "src/new_file.py", "Y = 2\n")  # untracked, not in brief
        self.make_slice()
        code, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(code, 1)
        self.assertIn("src/app.py", doc["changed_files"])
        self.assertIn("src/new_file.py", doc["changed_files"])
        self.assertEqual(check_of(doc, "scope")["status"], "fail")

    def test_out_of_scope_file_fails_scope(self) -> None:
        write(self.repo, "src/other.py", "X = 2\n")
        self.make_slice()
        code, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(code, 1)
        self.assertEqual(doc["verdict"], "fail")
        scope = check_of(doc, "scope")
        self.assertEqual(scope["status"], "fail")
        self.assertIn("src/other.py", scope["evidence"][0])

    def test_removed_merchant_id_filter_blocks(self) -> None:
        text = (self.repo / "src/app.py").read_text()
        write(self.repo, "src/app.py", text.replace('"merchant_id": merchant_id, ', ""))
        self.commit()
        self.make_slice()
        code, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(code, 3)
        self.assertEqual(doc["verdict"], "block")
        gate = check_of(doc, "hard_gate")
        self.assertEqual(gate["status"], "block")
        self.assertTrue(any(e.startswith("tenant:") for e in gate["evidence"]))

    def test_added_claude_settings_fails_sandbox_escape(self) -> None:
        write(self.repo, ".claude/settings.json", "{}\n")
        self.make_slice(files=["src/app.py", ".claude/settings.json"])
        code, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(code, 1)
        self.assertEqual(check_of(doc, "sandbox_escape")["status"], "fail")

    def test_package_json_scripts_change_fails_sandbox_escape(self) -> None:
        write(self.repo, "package.json", json.dumps({"name": "x", "scripts": {"test": "jest"}}))
        self.commit()
        self.make_slice(files=["package.json"])
        # move the base tag past the package.json commit, then change scripts only
        write(self.repo, "package.json", json.dumps({"name": "y", "scripts": {"test": "jest"}}))
        self.commit()
        _, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(check_of(doc, "sandbox_escape")["status"], "fail")  # new file vs base
        # a pure non-scripts change relative to a base that already has package.json passes
        self.make_slice()
        self.repo.joinpath(".slp/slices/s1/brief.md").write_text(
            "---\nslice_id: s1\ngoal: g\nfiles: [package.json]\nbase_ref: HEAD~1\n"
            "tests_must_pass: []\nout_of_scope: []\n---\n"
        )
        _, doc2, _ = self.verify()
        assert doc2 is not None
        self.assertEqual(check_of(doc2, "sandbox_escape")["status"], "pass")

    def test_missing_result_json_fails_contract(self) -> None:
        write(self.repo, "src/app.py", (self.repo / "src/app.py").read_text() + "\n# x\n")
        self.make_slice(result=False)
        code, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(code, 1)
        self.assertEqual(check_of(doc, "contract")["status"], "fail")
        self.assertIn("missing", check_of(doc, "contract")["evidence"][0])

    def test_invalid_result_json_fails_contract(self) -> None:
        self.make_slice()
        write(self.repo, ".slp/slices/s1/result.json", json.dumps({"slice_id": "s1"}))
        _, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(check_of(doc, "contract")["status"], "fail")

    def test_no_commands_configured_is_unconfigured_not_pass(self) -> None:
        bare = write(self.tmp, "cfg/bare.yaml", "repo: fixture\n")
        self.make_slice()
        for cfg, use in ((bare, True), (None, False)):  # empty config, then no config at all
            with self.subTest(config=str(cfg)):
                code, doc, _ = self.verify(config=cfg, use_config=use)
                assert doc is not None
                for name in ("test", "lint", "typecheck"):
                    self.assertEqual(check_of(doc, name)["status"], "unconfigured")
                self.assertNotEqual(doc["verdict"], "pass")
                self.assertEqual(code, 1)

    def test_writer_conflict_in_same_parallel_group_fails(self) -> None:
        write(self.repo, "src/app.py", (self.repo / "src/app.py").read_text() + "\n# a\n")
        self.make_slice("s1", ["src/app.py"], group="g1")
        self.make_slice("s2", ["src/app.py", "src/other.py"], group="g1")
        self.make_slice("s3", ["src/app.py"], group="g2")  # other group: ignored
        code, doc, _ = self.verify("s1")
        assert doc is not None
        self.assertEqual(code, 1)
        conflict = check_of(doc, "writer_conflict")
        self.assertEqual(conflict["status"], "fail")
        self.assertIn("s2", conflict["evidence"][0])
        self.assertNotIn("s3", " ".join(conflict["evidence"]))

    def test_failing_and_timing_out_commands_fail(self) -> None:
        cfg = write(
            self.tmp,
            "cfg/bad.yaml",
            'commands:\n  test: {cmd: "echo boom; exit 3", timeout: 30}\n'
            '  lint: {cmd: "sleep 5", timeout: 0.5}\n  typecheck: {skip: x}\n',
        )
        self.make_slice()
        code, doc, _ = self.verify(config=cfg)
        assert doc is not None
        self.assertEqual(code, 1)
        self.assertEqual(check_of(doc, "test")["status"], "fail")
        self.assertIn("boom", check_of(doc, "test")["evidence"][-1])
        lint = check_of(doc, "lint")
        self.assertEqual(lint["status"], "fail")
        self.assertIn("TIMEOUT", lint["evidence"][0])

    def test_round_defaults_increment(self) -> None:
        self.make_slice()
        self.verify()
        _, doc, _ = self.verify()
        assert doc is not None
        self.assertEqual(doc["round"], 2)
        _, doc, _ = self.verify(extra=["--round", "7"])
        assert doc is not None
        self.assertEqual(doc["round"], 7)
        self.assertTrue((self.repo / ".slp/slices/s1/verify.7.json").is_file())

    def test_repo_config_inside_worktree_is_rejected(self) -> None:
        self.make_slice()
        evil = write(self.repo, ".slp/evil.yaml", 'commands:\n  test: {cmd: "touch pwned"}\n')
        code, doc, out = self.verify(config=evil)
        self.assertEqual(code, 2, out)
        self.assertFalse((self.repo / "pwned").exists())

    def test_repo_config_outside_trusted_dir_is_rejected(self) -> None:
        self.make_slice()
        evil = write(self.tmp, "elsewhere/evil.yaml", 'commands:\n  test: {cmd: "touch pwned"}\n')
        code, _, out = self.verify(config=evil)
        self.assertEqual(code, 2, out)
        self.assertFalse((self.repo / "pwned").exists())

    def test_bad_brief_is_usage_error(self) -> None:
        write(self.repo, ".slp/slices/s1/brief.md", "no front matter\n")
        code, _, out = self.verify()
        self.assertEqual(code, 2, out)

    def test_repo_name_maps_worktree_to_main_repo(self) -> None:
        wt = self.tmp / "wt"
        from helpers import sh

        sh(self.repo, "git", "worktree", "add", "-q", "-b", "feat", str(wt), "base")
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import slp_git

        self.assertEqual(slp_git.repo_name(wt), "repo")
        self.assertEqual(slp_git.repo_name(self.repo), "repo")


if __name__ == "__main__":
    unittest.main()
