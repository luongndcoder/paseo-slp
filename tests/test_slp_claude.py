"""bin/slp-claude: --settings collection, merge semantics, pass-through, fail-closed."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "bin" / "slp-claude"


def load_wrapper():
    loader = importlib.machinery.SourceFileLoader("slp_claude", str(WRAPPER))
    spec = importlib.util.spec_from_loader("slp_claude", loader)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    loader.exec_module(mod)
    return mod


W = load_wrapper()

ROLE = {
    "skillOverrides": {"slp-supervisor": "on", "foo": "off"},
    "permissions": {"deny": ["Bash", "Write"]},
    "hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "role-start"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "role-guard"}]}],
    },
}


class TakeFlagTests(unittest.TestCase):
    def test_both_forms_and_multiple_occurrences(self) -> None:
        rest, vals = W.take_flag(
            ["-p", "--settings", "a.json", "x", "--settings={\"k\":1}", "--settings", "b.json", "y"],
            "--settings",
        )
        self.assertEqual(rest, ["-p", "x", "y"])
        self.assertEqual(vals, ["a.json", '{"k":1}', "b.json"])

    def test_stops_at_double_dash(self) -> None:
        rest, vals = W.take_flag(["--settings", "a", "--", "--settings", "b"], "--settings")
        self.assertEqual(rest, ["--", "--settings", "b"])
        self.assertEqual(vals, ["a"])

    def test_missing_value_fails(self) -> None:
        with self.assertRaises(W.WrapperError):
            W.take_flag(["--settings"], "--settings")

    def test_does_not_match_prefix_flags(self) -> None:
        rest, vals = W.take_flag(["--settings-extra", "v", "--setting-sources", "user"], "--settings")
        self.assertEqual(vals, [])
        self.assertEqual(rest, ["--settings-extra", "v", "--setting-sources", "user"])


class LoadSettingsTests(unittest.TestCase):
    def test_json_string_vs_path(self) -> None:
        self.assertEqual(W.load_settings('  {"a": 1}', "x"), {"a": 1})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text('{"b": 2}')
            self.assertEqual(W.load_settings(str(p), "x"), {"b": 2})

    def test_bad_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text("{nope")
            arr = Path(d) / "arr.json"
            arr.write_text("[1]")
            for value in (str(bad), str(arr), str(Path(d) / "missing.json"), "{broken"):
                with self.assertRaises(W.WrapperError, msg=value):
                    W.load_settings(value, "x")


class MergeTests(unittest.TestCase):
    def test_deny_dedupe_and_order(self) -> None:
        paseo = {"permissions": {"deny": ["Read", "Bash"], "allow": ["Grep"]}}
        role = {"permissions": {"deny": ["Bash", "Write"], "allow": ["Grep", "Glob"]}}
        m = W.merge_settings([paseo], role)
        self.assertEqual(m["permissions"]["deny"], ["Read", "Bash", "Write"])
        self.assertEqual(m["permissions"]["allow"], ["Grep", "Glob"])

    def test_hooks_appended_per_event(self) -> None:
        paseo = {"hooks": {"PreToolUse": [{"matcher": "X", "hooks": []}], "Stop": [{"hooks": []}]}}
        m = W.merge_settings([paseo], ROLE)
        self.assertEqual([e.get("matcher") for e in m["hooks"]["PreToolUse"]], ["X", "Bash"])
        self.assertEqual(len(m["hooks"]["Stop"]), 1)
        self.assertEqual(m["hooks"]["SessionStart"], ROLE["hooks"]["SessionStart"])

    def test_skill_overrides_role_wins(self) -> None:
        paseo = {"skillOverrides": {"foo": "on", "bar": "on"}}
        m = W.merge_settings([paseo], ROLE)
        self.assertEqual(m["skillOverrides"], {"foo": "off", "bar": "on", "slp-supervisor": "on"})

    def test_paseo_keys_preserved(self) -> None:
        paseo = {"fastMode": False, "ultracode": True, "disableAllHooks": True}
        role = {**ROLE, "fastMode": True, "disableAllHooks": False, "ultracode": False}
        m = W.merge_settings([paseo], role)
        self.assertIs(m["fastMode"], False)
        self.assertIs(m["ultracode"], True)
        self.assertIs(m["disableAllHooks"], True)

    def test_role_only_keys_pass_when_paseo_silent(self) -> None:
        m = W.merge_settings([{"fastMode": True}], {"disableAllHooks": False, "x": {"y": 1}})
        self.assertIs(m["fastMode"], True)
        self.assertEqual(m["x"], {"y": 1})
        self.assertIs(m["disableAllHooks"], False)

    def test_other_keys_role_wins_and_nested_merge(self) -> None:
        paseo = {"sandbox": {"enabled": True, "a": 1}, "model": "m1"}
        role = {"sandbox": {"a": 2}, "model": "m2"}
        m = W.merge_settings([paseo], role)
        self.assertEqual(m["sandbox"], {"enabled": True, "a": 2})
        self.assertEqual(m["model"], "m2")

    def test_multiple_paseo_layers_fold(self) -> None:
        m = W.merge_settings(
            [{"permissions": {"deny": ["A"]}, "fastMode": True}, {"permissions": {"deny": ["B"]}, "fastMode": False}],
            {"permissions": {"deny": ["A", "C"]}},
        )
        self.assertEqual(m["permissions"]["deny"], ["A", "B", "C"])
        self.assertIs(m["fastMode"], False)


class BuildArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.role = Path(self.dir.name) / "role.json"
        self.role.write_text(json.dumps(ROLE))

    def test_no_paseo_settings(self) -> None:
        argv, merged = W.build_argv([W.ROLE_FLAG, str(self.role), "--append-system-prompt", "RM", "-p", "hi"])
        self.assertEqual(merged, ROLE)
        self.assertEqual(argv[0], "--settings")
        self.assertEqual(json.loads(argv[1]), ROLE)
        self.assertEqual(argv[2:], ["--append-system-prompt", "RM", "-p", "hi"])

    def test_paseo_settings_collapsed_into_one(self) -> None:
        paseo = json.dumps({"fastMode": False, "disableAllHooks": True})
        argv, _ = W.build_argv(
            [f"{W.ROLE_FLAG}={self.role}", "--settings", paseo, "--model", "m", f"--settings={paseo}"]
        )
        self.assertEqual(argv.count("--settings"), 1)
        self.assertEqual(argv[2:], ["--model", "m"])
        out = json.loads(argv[1])
        self.assertIs(out["fastMode"], False)
        self.assertIs(out["disableAllHooks"], True)
        self.assertEqual(out["permissions"]["deny"], ["Bash", "Write"])

    def test_bad_role_file_fails_closed(self) -> None:
        self.role.write_text("not json")
        with self.assertRaises(W.WrapperError):
            W.build_argv([W.ROLE_FLAG, str(self.role)])
        with self.assertRaises(W.WrapperError):
            W.build_argv([W.ROLE_FLAG, str(self.role) + ".missing"])
        with self.assertRaises(W.WrapperError):
            W.build_argv(["--version"])  # no role flag at all


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, sys
print(json.dumps(sys.argv[1:]))
"""


class ExecTests(unittest.TestCase):
    """Run the wrapper as a subprocess against a fake `claude` that echoes its argv."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = Path(self.dir.name)
        self.fake = d / "claude"
        self.fake.write_text(FAKE_CLAUDE)
        self.fake.chmod(0o755)
        self.role = d / "role.json"
        self.role.write_text(json.dumps(ROLE))

    def run_wrapper(self, *args: str, role: Path | None = None, extra_env: dict | None = None):
        env = {**os.environ, "SLP_REAL_CLAUDE": str(self.fake), **(extra_env or {})}
        return subprocess.run(
            [sys.executable, str(WRAPPER), "--slp-role-settings", str(role or self.role), *args],
            capture_output=True, text=True, env=env,
        )

    def test_version_and_auth_pass_through(self) -> None:
        for tail in (["--version"], ["auth", "status"]):
            res = self.run_wrapper("--append-system-prompt", "RM", *tail)
            self.assertEqual(res.returncode, 0, res.stderr)
            argv = json.loads(res.stdout)
            self.assertEqual(argv[0], "--settings")
            self.assertEqual(argv[2:], ["--append-system-prompt", "RM", *tail])

    def test_end_to_end_merge(self) -> None:
        paseo = json.dumps({"fastMode": True})
        res = self.run_wrapper("--settings", paseo, "-p", "x")
        self.assertEqual(res.returncode, 0, res.stderr)
        argv = json.loads(res.stdout)
        self.assertEqual(argv.count("--settings"), 1)
        merged = json.loads(argv[1])
        self.assertIs(merged["fastMode"], True)
        self.assertEqual(merged["permissions"], ROLE["permissions"])
        self.assertEqual(argv[2:], ["-p", "x"])

    def test_fail_closed_on_bad_role_file(self) -> None:
        bad = Path(self.dir.name) / "bad.json"
        bad.write_text("{oops")
        res = self.run_wrapper("--version", role=bad)
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        self.assertIn("refusing to start claude", res.stderr)

    def test_fail_closed_on_missing_role_file(self) -> None:
        res = self.run_wrapper("--version", role=Path(self.dir.name) / "nope.json")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")

    def test_real_claude_lookup_skips_itself(self) -> None:
        bindir = Path(self.dir.name) / "bin"
        bindir.mkdir()
        (bindir / "claude").symlink_to(WRAPPER)  # a `claude` that is the wrapper itself
        other = Path(self.dir.name) / "other"
        other.mkdir()
        (other / "claude").write_text(FAKE_CLAUDE)
        (other / "claude").chmod(0o755)
        env = {"PATH": f"{bindir}{os.pathsep}{other}"}
        self.assertEqual(W.find_real_claude(env, str(WRAPPER)), str(other / "claude"))

    def test_missing_real_claude_fails(self) -> None:
        with self.assertRaises(W.WrapperError):
            W.find_real_claude({"PATH": self.dir.name + "/nowhere"}, str(WRAPPER))


if __name__ == "__main__":
    unittest.main()
