from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from helpers import EVIDENCE, RepoCase, write  # noqa: E402
from slp_schema import schema_errors  # noqa: E402


def finding(file: str, line: int, evidence: str) -> dict[str, Any]:
    return {
        "lens": "security", "severity": "major", "file": file, "line": line,
        "evidence": evidence, "fix_hint": "fix it",
    }  # fmt: skip


class EvidenceTests(RepoCase):
    def run_evidence(self, findings: list[dict[str, Any]], brief: Path | None = None) -> dict:
        review = write(
            self.repo,
            ".slp/slices/s1/review.1.json",
            json.dumps({"slice_id": "s1", "round": 1, "verdict": "fix", "findings": findings}),
        )
        cmd = [sys.executable, str(EVIDENCE), str(review), str(self.repo)]
        if brief:
            cmd += ["--brief", str(brief)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = review.with_name("review.1.checked.json")
        self.assertTrue(out.is_file())
        return json.loads(out.read_text())

    def prepare(self) -> None:
        text = (self.repo / "src/app.py").read_text().replace("active", "enabled")
        write(self.repo, "src/app.py", text)
        self.commit()
        self.make_slice()

    def test_keeps_correct_rejects_wrong_line_and_fabricated(self) -> None:
        self.prepare()
        good = finding("src/app.py", 2, 'query =  {"merchant_id": merchant_id, "enabled": True}')
        wrong_line = finding("src/app.py", 20, "return 1")  # file has 7 lines
        far_line = finding("src/app.py", 1, "def helper():")  # real line, but > 3 lines away
        fabricated = finding("src/app.py", 2, "db.campaigns.delete_many({})")
        outside = finding("src/other.py", 1, "X = 1")  # not in diff, not in brief
        checked = self.run_evidence([good, wrong_line, far_line, fabricated, outside])
        self.assertEqual(checked["findings"], [good])
        reasons = {r["finding"]["evidence"]: r["reason"] for r in checked["rejected"]}
        self.assertEqual(len(checked["rejected"]), 4)
        self.assertIn("does not exist", reasons["return 1"])
        self.assertIn("not found within", reasons["def helper():"])
        self.assertIn("not found within", reasons["db.campaigns.delete_many({})"])
        self.assertIn("not in the diff", reasons["X = 1"])
        self.assertEqual(schema_errors(checked, "review"), [])
        self.assertEqual(checked["verdict"], "fix")

    def test_whitespace_normalised_and_multiline_evidence(self) -> None:
        self.prepare()
        multi = finding(
            "src/app.py", 2, 'def find_campaigns(db, merchant_id):\n    query = {"merchant_id"'
        )
        checked = self.run_evidence([multi])
        self.assertEqual(len(checked["findings"]), 1)

    def test_empty_or_tiny_evidence_rejected(self) -> None:
        self.prepare()
        checked = self.run_evidence(
            [finding("src/app.py", 2, ""), finding("src/app.py", 2, "  q ")]
        )
        self.assertEqual(checked["findings"], [])
        self.assertEqual(len(checked["rejected"]), 2)

    def test_brief_files_allow_file_outside_diff(self) -> None:
        self.prepare()
        brief = write(
            self.repo, "other-brief.md",
            "---\nslice_id: s1\ngoal: g\nfiles: [src/other.py]\nbase_ref: base\n"
            "tests_must_pass: []\nout_of_scope: []\n---\n",
        )  # fmt: skip
        checked = self.run_evidence([finding("src/other.py", 1, "X = 1")], brief=brief)
        self.assertEqual(len(checked["findings"]), 1)

    def test_uncommitted_file_is_judged_against_working_tree(self) -> None:
        self.make_slice()
        write(self.repo, "src/app.py", (self.repo / "src/app.py").read_text() + "NEW_CONST = 5\n")
        checked = self.run_evidence([finding("src/app.py", 8, "NEW_CONST = 5")])
        self.assertEqual(len(checked["findings"]), 1)

    def test_base_option_covers_committed_changes_without_brief(self) -> None:
        text = (self.repo / "src/app.py").read_text() + "COMMITTED = 1\n"
        write(self.repo, "src/app.py", text)
        self.commit()
        review = write(
            self.repo,
            ".slp/final-review.json",
            json.dumps(
                {
                    "slice_id": "workstream", "round": 1, "verdict": "fix",
                    "findings": [finding("src/app.py", 8, "COMMITTED = 1")],
                }
            ),
        )  # fmt: skip
        for extra, expected in (([], 0), (["--base", "base"], 1)):  # default base HEAD sees nothing
            subprocess.run(
                [sys.executable, str(EVIDENCE), str(review), str(self.repo), *extra],
                capture_output=True, text=True, check=True,
            )  # fmt: skip
            out = json.loads(review.with_name("final-review.checked.json").read_text())
            self.assertEqual(len(out["findings"]), expected, extra)

    def test_invalid_review_is_usage_error(self) -> None:
        bad = write(self.repo, "bad.json", json.dumps({"slice_id": "s1"}))
        res = subprocess.run(
            [sys.executable, str(EVIDENCE), str(bad), str(self.repo)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
