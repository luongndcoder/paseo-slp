from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import METRICS  # noqa: E402


def report(reviews: list[dict] | None) -> str:
    metrics = "metrics: {rounds_total: 1}" if reviews is None else "metrics:\n  reviews:\n" + "".join(
        f"    - {json.dumps(r)}\n" for r in reviews
    )
    return (
        "---\nstatus: done\nslices: []\nacceptance: []\nopen_questions: []\nhard_gates_hit: []\n"
        f"deferred_minor: []\n{metrics}\n---\nbody\n"
    )


def rev(profile: str, total: int, accepted: int, changed: bool) -> dict:
    return {"reviewer_profile": profile, "findings_total": total, "findings_accepted": accepted,
            "changed_outcome": changed}  # fmt: skip


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="slp-metrics-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def put(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(METRICS), *args], capture_output=True, text=True)

    def test_aggregates_per_reviewer_profile_across_reports(self) -> None:
        self.put("a/.slp/report.md", report([rev("slp-checker-sonnet", 4, 2, True), rev("slp-final-reviewer", 2, 0, False)]))
        self.put("b/.slp/report.md", report([rev("slp-checker-sonnet", 0, 0, False), rev("slp-checker-sonnet", 6, 3, True)]))
        res = self.run_cli(str(self.root), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["reports"], 2)
        rows = {r["reviewer_profile"]: r for r in data["reviewers"]}
        sonnet = rows["slp-checker-sonnet"]
        self.assertEqual((sonnet["reviews"], sonnet["changed_outcome"]), (3, 2))
        self.assertAlmostEqual(sonnet["change_rate"], 2 / 3)
        self.assertEqual((sonnet["findings_total"], sonnet["findings_accepted"]), (10, 5))
        self.assertAlmostEqual(sonnet["rejected_by_evidence_rate"], 0.5)
        final = rows["slp-final-reviewer"]
        self.assertEqual(final["change_rate"], 0.0)
        self.assertEqual(final["rejected_by_evidence_rate"], 1.0)

    def test_text_table_has_one_row_per_profile(self) -> None:
        self.put("r/report.md", report([rev("slp-checker-opus", 4, 1, True), rev("slp-checker-sonnet", 0, 0, False)]))
        res = self.run_cli(str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.strip().splitlines()
        self.assertIn("change rate", lines[1])
        opus = next(ln for ln in lines if ln.startswith("slp-checker-opus"))
        self.assertIn("100%", opus)  # change rate
        self.assertIn("75%", opus)  # rejected-by-evidence rate (3 of 4)
        sonnet = next(ln for ln in lines if ln.startswith("slp-checker-sonnet"))
        self.assertIn("0%", sonnet)  # change rate 0%, rejected rate undefined -> "-"
        self.assertIn("-", sonnet)

    def test_accepts_explicit_report_files(self) -> None:
        one = self.put("x/report.md", report([rev("p", 1, 1, True)]))
        self.put("y/report.md", report([rev("q", 1, 1, True)]))
        data = json.loads(self.run_cli(str(one), "--json").stdout)
        self.assertEqual([r["reviewer_profile"] for r in data["reviewers"]], ["p"])

    def test_skips_old_broken_and_invalid_entries_without_failing(self) -> None:
        self.put("old/report.md", report(None))
        self.put("broken/report.md", "no front matter here\n")
        bad = {"reviewer_profile": "p", "findings_total": 1, "findings_accepted": 3, "changed_outcome": True}
        self.put("mixed/report.md", report([bad, rev("p", 2, 1, False)]))
        res = self.run_cli(str(self.root), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["skipped"], {"reports_unreadable": 1, "reports_without_reviews": 1, "entries_invalid": 1})
        self.assertEqual(data["reviewers"][0]["reviews"], 1)
        self.assertIn("skipped", res.stderr)

    def test_no_report_found_is_usage_error(self) -> None:
        self.assertEqual(self.run_cli(str(self.root)).returncode, 2)

    def test_template_report_is_readable(self) -> None:
        template = Path(__file__).resolve().parent.parent / "templates" / "report.md"
        data = json.loads(self.run_cli(str(template), "--json").stdout)
        self.assertEqual({r["reviewer_profile"] for r in data["reviewers"]}, {"slp-checker-sonnet", "slp-final-reviewer"})


if __name__ == "__main__":
    unittest.main()
