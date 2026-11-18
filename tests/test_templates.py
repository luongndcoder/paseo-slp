from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import yaml  # noqa: E402
from slp_schema import SCHEMAS, load_schema, parse_front_matter, schema_errors  # noqa: E402

TEMPLATES = ROOT / "templates"
FILES = {
    "workstream.md": "workstream", "brief.md": "brief", "report.md": "report",
    "result.json": "result", "verify.json": "verify", "review.json": "review",
}  # fmt: skip


class TemplateTests(unittest.TestCase):
    def test_every_template_validates(self) -> None:
        for name, kind in FILES.items():
            with self.subTest(template=name):
                text = (TEMPLATES / name).read_text()
                data = parse_front_matter(text)[0] if name.endswith(".md") else json.loads(text)
                self.assertEqual(schema_errors(data, kind), [])

    def test_schemas_are_valid_draft_2020_12(self) -> None:
        from jsonschema import Draft202012Validator

        for kind in SCHEMAS:
            Draft202012Validator.check_schema(load_schema(kind))

    def test_invalid_examples_are_rejected(self) -> None:
        brief = parse_front_matter((TEMPLATES / "brief.md").read_text())[0]
        self.assertNotEqual(schema_errors({**brief, "files": []}, "brief"), [])
        self.assertNotEqual(schema_errors({**brief, "extra": 1}, "brief"), [])
        result = json.loads((TEMPLATES / "result.json").read_text())
        self.assertNotEqual(schema_errors({**result, "status": "ok"}, "result"), [])
        self.assertNotEqual(schema_errors({**result, "summary": "a\n" * 6}, "result"), [])

    def test_brief_has_three_parts(self) -> None:
        brief = parse_front_matter((TEMPLATES / "brief.md").read_text())[0]
        for key in ("goal", "constraints", "design_choice"):
            without = {k: v for k, v in brief.items() if k != key}
            self.assertNotEqual(schema_errors(without, "brief"), [], key)
        self.assertNotEqual(schema_errors({**brief, "constraints": []}, "brief"), [])
        self.assertNotEqual(schema_errors({**brief, "design_choice": ""}, "brief"), [])

    def test_version_pin_is_required_and_full_sha(self) -> None:
        for name, kind in (("verify.json", "verify"), ("review.json", "review")):
            doc = json.loads((TEMPLATES / name).read_text())
            self.assertEqual(len(doc["head_sha"]), 40)
            for key in ("head_sha", "base_ref") if kind == "review" else ("head_sha",):
                self.assertNotEqual(schema_errors({k: v for k, v in doc.items() if k != key}, kind), [], (name, key))
            self.assertNotEqual(schema_errors({**doc, "head_sha": doc["head_sha"][:8]}, kind), [])
            self.assertNotEqual(schema_errors({**doc, "head_sha": "HEAD"}, kind), [])

    def test_scope_challenge_is_optional_and_shaped(self) -> None:
        result = json.loads((TEMPLATES / "result.json").read_text())
        self.assertEqual(schema_errors(result, "result"), [])
        ok = {"premise": "p", "evidence": "f.py:3 x = 1", "proposal": "q"}
        self.assertEqual(schema_errors({**result, "scope_challenge": ok}, "result"), [])
        self.assertNotEqual(schema_errors({**result, "scope_challenge": {"premise": "p"}}, "result"), [])
        self.assertNotEqual(schema_errors({**result, "scope_challenge": {**ok, "extra": 1}}, "result"), [])
        self.assertNotEqual(schema_errors({**result, "scope_challenge": {**ok, "proposal": ""}}, "result"), [])

    def test_report_metrics_reviews_required_and_shaped(self) -> None:
        report = parse_front_matter((TEMPLATES / "report.md").read_text())[0]
        self.assertGreaterEqual(len(report["metrics"]["reviews"]), 1)
        no_reviews = {**report, "metrics": {"rounds_total": 1}}
        self.assertNotEqual(schema_errors(no_reviews, "report"), [])
        entry = report["metrics"]["reviews"][0]
        for key in ("reviewer_profile", "findings_total", "findings_accepted", "changed_outcome"):
            broken = {**report, "metrics": {"reviews": [{k: v for k, v in entry.items() if k != key}]}}
            self.assertNotEqual(schema_errors(broken, "report"), [], key)
        bad_type = {**report, "metrics": {"reviews": [{**entry, "changed_outcome": "yes"}]}}
        self.assertNotEqual(schema_errors(bad_type, "report"), [])
        self.assertEqual(schema_errors({**report, "metrics": {"reviews": []}}, "report"), [])

    def test_default_repo_config_regexes_compile(self) -> None:
        import re

        for fname in ("_default.yaml", "TEMPLATE.yaml"):
            cfg = yaml.safe_load((ROOT / "repos" / fname).read_text())
            for rule in cfg.get("hard_gate_rules", []):
                for pat in rule.get("paths", []):
                    re.compile(pat)
                for spec in rule.get("lines", []):
                    re.compile(spec["re"])


if __name__ == "__main__":
    unittest.main()
