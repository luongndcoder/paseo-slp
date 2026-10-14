"""slp-build pieces: explicit skill "on" overrides, slp-* skill symlinks, ~/.claude/settings.json overrides."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import slp_skills_install as inst  # noqa: E402


def load_build():
    loader = importlib.machinery.SourceFileLoader("slp_build", str(ROOT / "bin" / "slp-build"))
    spec = importlib.util.spec_from_loader("slp_build", loader)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    loader.exec_module(mod)
    return mod


class BuildSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = load_build()
        cls.roles = cls.build.load_roles()
        cls.skills = cls.build.all_skill_names()

    def overrides(self, role: str) -> dict[str, str]:
        settings, _, _ = self.build.claude_artifacts(self.roles[role], self.skills)
        return settings["skillOverrides"]

    def test_every_auto_skill_is_explicitly_on(self) -> None:
        for role, policy in self.roles.items():
            if policy["family"] != "claude":
                continue
            ov = self.overrides(role)
            for skill in policy.get("skills_auto", []):
                self.assertEqual(ov.get(skill), "on", f"{role}: {skill}")
            self.assertNotIn("off", {ov[s] for s in policy.get("skills_auto", [])})

    def test_slp_playbooks_only_for_their_tier(self) -> None:
        self.assertEqual(self.overrides("supervisor")["slp-supervisor"], "on")
        self.assertEqual(self.overrides("supervisor")["slp-lead"], "off")
        self.assertEqual(self.overrides("lead")["slp-lead"], "on")
        self.assertEqual(self.overrides("lead")["slp-supervisor"], "off")
        for role in ("claude-builder", "claude-checker", "claude-final-reviewer", "claude-integration-reviewer"):
            self.assertEqual(self.overrides(role)["slp-lead"], "off")
            self.assertEqual(self.overrides(role)["slp-supervisor"], "off")

    def test_checker_policy_is_slp_only_with_readonly_bash(self) -> None:
        for role in ("claude-checker", "claude-final-reviewer", "claude-integration-reviewer"):
            self.assertEqual(self.roles[role]["write"], "slp-only")
            self.assertEqual(self.roles[role]["bash"], "readonly")
            settings, guard_policy, _ = self.build.claude_artifacts(self.roles[role], self.skills)
            self.assertNotIn("Write", settings["permissions"]["deny"])
            matchers = [h["matcher"] for h in settings["hooks"]["PreToolUse"]]
            self.assertIn("Edit|Write|MultiEdit|NotebookEdit", matchers)
            self.assertEqual(guard_policy["slp_scope"], "cwd")
        _, lead_policy, _ = self.build.claude_artifacts(self.roles["lead"], self.skills)
        self.assertEqual(lead_policy["slp_scope"], "repo")

    def test_codex_checker_mirrors_advisor_flags(self) -> None:
        self.assertEqual(self.roles["codex-checker"]["provider_id"], "slp-codex-checker")
        adv = self.build.codex_provider(self.roles["codex-advisor"])["command"]
        chk = self.build.codex_provider(self.roles["codex-checker"])["command"]
        self.assertEqual(len(adv), len(chk))
        keys = lambda cmd: [a.split("=", 1)[0] for a in cmd if a != "-c"]  # noqa: E731
        self.assertEqual(keys(adv), keys(chk))
        joined = " ".join(chk)
        for needle in ('sandbox_mode="read-only"', 'approval_policy="never"', "features.multi_agent=false",
                       "model_catalog_json=", "developer_instructions=", "model_instructions_file="):  # fmt: skip
            self.assertIn(needle, joined)
        self.assertIn("codex-checker/role.md", chk[-1])


class SymlinkTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="slp-inst-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.src = self.tmp / "skills-src"
        self.dst = self.tmp / "claude-skills"
        for name in ("slp-lead", "slp-supervisor", "not-a-skill-dir"):
            (self.src / name).mkdir(parents=True)
        (self.src / "slp-lead" / "SKILL.md").write_text("x")
        (self.src / "slp-supervisor" / "SKILL.md").write_text("x")  # not-a-skill-dir has no SKILL.md
        self.dst.mkdir()

    def test_creates_only_slp_skills_and_is_idempotent(self) -> None:
        (self.dst / "be-git").mkdir()
        lines = inst.install_symlinks(self.src, self.dst, apply=True)
        self.assertEqual(len(lines), 2)
        self.assertEqual((self.dst / "slp-lead").resolve(), self.src / "slp-lead")
        self.assertTrue((self.dst / "slp-supervisor").is_symlink())
        self.assertFalse((self.dst / "be-git").is_symlink())
        again = inst.install_symlinks(self.src, self.dst, apply=True)
        self.assertTrue(all(" ok:" in ln for ln in again), again)

    def test_dry_run_changes_nothing(self) -> None:
        lines = inst.install_symlinks(self.src, self.dst, apply=False)
        self.assertEqual(len(lines), 2)
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_replaces_stale_and_removes_dangling_slp_symlinks_only(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.dst / "slp-lead").symlink_to(elsewhere)  # stale target
        (self.dst / "slp-old").symlink_to(self.tmp / "gone")  # dangling, no longer shipped
        (self.dst / "other-link").symlink_to(self.tmp / "gone")  # not slp-*: untouched
        inst.install_symlinks(self.src, self.dst, apply=True)
        self.assertEqual((self.dst / "slp-lead").resolve(), self.src / "slp-lead")
        self.assertFalse((self.dst / "slp-old").is_symlink())
        self.assertTrue((self.dst / "other-link").is_symlink())

    def test_never_touches_real_directory(self) -> None:
        real = self.dst / "slp-lead"
        real.mkdir()
        (real / "keep.txt").write_text("mine")
        lines = inst.install_symlinks(self.src, self.dst, apply=True)
        self.assertTrue(any("refuse: " in ln and "slp-lead" in ln for ln in lines))
        self.assertFalse(real.is_symlink())
        self.assertEqual((real / "keep.txt").read_text(), "mine")


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="slp-inst-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "settings.json"
        self.original = {
            "env": {"A": "1"},
            "permissions": {"deny": ["Bash(sudo *)"]},
            "skillOverrides": {"pdf": "user-invocable-only"},
            "unicode": "Việt",
        }
        self.path.write_text(json.dumps(self.original, indent=2, ensure_ascii=False) + "\n")

    def test_adds_only_the_two_overrides(self) -> None:
        diff, changed = inst.ensure_overrides(self.path, apply=True)
        self.assertTrue(changed)
        got = json.loads(self.path.read_text())
        want = json.loads(json.dumps(self.original))
        want["skillOverrides"]["slp-supervisor"] = "user-invocable-only"
        want["skillOverrides"]["slp-lead"] = "user-invocable-only"
        self.assertEqual(got, want)
        added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        self.assertEqual(len(added), 3, diff)  # 2 new keys + the previous line gaining a comma
        self.assertIn("Việt", self.path.read_text())

    def test_idempotent_and_dry_run_does_not_write(self) -> None:
        before = self.path.read_text()
        diff, changed = inst.ensure_overrides(self.path, apply=False)
        self.assertTrue(changed)
        self.assertEqual(self.path.read_text(), before)
        inst.ensure_overrides(self.path, apply=True)
        after = self.path.read_text()
        diff, changed = inst.ensure_overrides(self.path, apply=True)
        self.assertFalse(changed)
        self.assertEqual(self.path.read_text(), after)

    def test_creates_skilloverrides_when_missing(self) -> None:
        self.path.write_text('{"env": {}}\n')
        inst.ensure_overrides(self.path, apply=True)
        self.assertEqual(
            json.loads(self.path.read_text())["skillOverrides"],
            {"slp-supervisor": "user-invocable-only", "slp-lead": "user-invocable-only"},
        )


if __name__ == "__main__":
    unittest.main()
