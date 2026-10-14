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
