"""Each default hard-gate category fires on a representative diff and stays quiet on benign ones."""
from __future__ import annotations

import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import slp_rules  # noqa: E402
from slp_git import DiffLine  # noqa: E402

RULES = slp_rules.read_yaml(slp_rules.REPOS_DIR / "_default.yaml")["hard_gate_rules"]


def hits(
    files: list[str], added: Sequence[str] = (), removed: Sequence[str] = ()
) -> set[str]:
    lines = [DiffLine(files[0] if files else "x.py", "added", 1, t) for t in added]
    lines += [DiffLine(files[0] if files else "x.py", "removed", 1, t) for t in removed]
    return {e.split(":", 1)[0] for e in slp_rules.scan_hard_gates(RULES, files, lines)}


class HardGateRuleTests(unittest.TestCase):
    def test_categories_fire(self) -> None:
        cases = {
            "tenant": hits(["a.py"], removed=['q = {"merchant_id": mid}']),
            "schema_index": hits(["a.py"], added=["col.create_index('k')"]),
            "schema_index_path": hits(["db/migrations/0001.py"]),
            "kafka_contract": hits(["a.py"], added=['ORDER_TOPIC = "order.created"']),
            "kafka_path": hits(["schemas/order.avsc"]),
            "auth": hits(["a.py"], added=["token = jwt.decode(raw)"]),
            "deploy_config": hits(["deploy/k8s/app.yaml"]),
            "deploy_dockerfile": hits(["Dockerfile"]),
            "pii_log": hits(["a.py"], added=["logger.info('got %s', user.cccd)"]),
            "pii_field": hits(["a.py"], added=["    'phone_number': row.phone,"]),
            "public_api": hits(["a.py"], added=["@router.post('/campaigns')"]),
            "public_api_route": hits(["a.py"], added=["@bp.route('/x', methods=['GET'])"]),
            "core_banking": hits(["a.py"], added=["core_banking_client.update_balance(acc)"]),
        }
        expect = {
            "tenant": "tenant", "schema_index": "schema_index", "schema_index_path": "schema_index",
            "kafka_contract": "kafka_contract", "kafka_path": "kafka_contract", "auth": "auth",
            "deploy_config": "deploy_config", "deploy_dockerfile": "deploy_config",
            "pii_log": "pii", "pii_field": "pii", "public_api": "public_api",
            "public_api_route": "public_api", "core_banking": "core_banking",
        }  # fmt: skip
        for name, got in cases.items():
            with self.subTest(case=name):
                self.assertIn(expect[name], got)

    def test_benign_changes_do_not_fire(self) -> None:
        self.assertEqual(hits(["a.py"], added=["x = compute(1)", "# fix typo"]), set())
        # adding a tenant filter is fine; only removed/changed lines are a gate
        self.assertNotIn("tenant", hits(["a.py"], added=['q["merchant_id"] = mid']))
        # .slp/ artifacts are never scanned
        self.assertEqual(hits([".slp/slices/s1/brief.md"], removed=["merchant_id"]), set())


if __name__ == "__main__":
    unittest.main()
