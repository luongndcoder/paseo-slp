"""slp-build wiring of bin/slp-claude + slp-conformance --profile-models helpers (no Paseo, no network)."""
from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))


def load(name: str, rel: str):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / rel))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    loader.exec_module(mod)
    return mod


class BuildWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = load("slp_build_w", "bin/slp-build")
        cls.roles = cls.build.load_roles()
        cls.skills = cls.build.all_skill_names()

    def test_claude_command_goes_through_wrapper(self) -> None:
        claude_roles = [r for r, p in self.roles.items() if p["family"] == "claude"]
        self.assertEqual(len(claude_roles), 7)
        for role in claude_roles:
            _, _, provider = self.build.claude_artifacts(self.roles[role], self.skills)
            cmd = provider["command"]
            self.assertEqual(cmd[0], str(ROOT / "bin" / "slp-claude"))
            self.assertEqual(cmd[1], "--slp-role-settings")
            self.assertEqual(cmd[2], str(ROOT / "build" / "claude" / f"{role}.settings.json"))
            self.assertEqual(cmd[3], "--append-system-prompt")
            self.assertNotIn("--settings", cmd)
            self.assertNotIn("claude", cmd)

    def test_wrapper_is_executable(self) -> None:
        self.assertTrue((ROOT / "bin" / "slp-claude").stat().st_mode & 0o111)

    def test_align_order_keeps_live_key_order(self) -> None:
        old = {"command": [1], "env": {"b": 1, "a": 2}, "paseoTools": {}}
        new = {"command": [2], "paseoTools": {}, "env": {"a": 2, "b": 1}, "label": "x"}
        out = self.build.align_order(new, old)
        self.assertEqual(list(out), ["command", "env", "paseoTools", "label"])
        self.assertEqual(list(out["env"]), ["b", "a"])
        self.assertEqual(out["command"], [2])

    def test_only_claude_leaves_other_providers_untouched(self) -> None:
        live = {"agents": {"providers": {
            "slp-lead": {"extends": "claude", "command": ["claude"]},
            "slp-opencode-builder": {"extends": "opencode", "label": "old"},
        }}}
        providers = {
            "slp-lead": {"extends": "claude", "command": ["slp-claude"]},
            "slp-opencode-builder": {"extends": "opencode", "label": "new"},
        }
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps(live))
            with mock.patch.object(self.build, "CONFIG", cfg):
                _, new, _ = self.build.merged_config(providers, only_claude=True)
                _, new_all, _ = self.build.merged_config(providers)
        got = json.loads(new)["agents"]["providers"]
        self.assertEqual(got["slp-lead"]["command"], ["slp-claude"])
        self.assertEqual(got["slp-opencode-builder"]["label"], "old")
        self.assertEqual(json.loads(new_all)["agents"]["providers"]["slp-opencode-builder"]["label"], "new")


class ConformanceProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.conf = load("slp_conf_w", "bin/slp-conformance")

    PROFILES = [
        {"id": "slp-supervisor", "provider": "slp-supervisor", "model": "claude-opus-5-5",
         "modeId": "bypassPermissions", "thinkingOptionId": "high"},
        {"id": "slp-checker-sonnet", "provider": "slp-claude-checker", "model": "claude-sonnet-5-5",
         "modeId": "bypassPermissions", "thinkingOptionId": "high"},
        {"id": "slp-checker-opus", "provider": "slp-claude-checker", "model": "claude-opus-5-5",
         "modeId": "bypassPermissions", "thinkingOptionId": "medium"},
        {"id": "slp-advisor-sonnet", "provider": "slp-claude-advisor", "model": "claude-sonnet-5-5",
         "modeId": "bypassPermissions", "thinkingOptionId": "high"},
        {"id": "slp-builder-flash", "provider": "antigravity", "model": "gemini-3.8-flash-high", "modeId": "full-access"},
        {"id": "slp-builder-pi-flash", "provider": "slp-pi-builder", "model": "openai-api/gemini-3.8-flash-high"},
        {"id": "slp-builder-deepseek", "provider": "slp-opencode-builder", "model": "opencode-go/deepseek-v4.1-flash",
         "modeId": "slp-builder", "thinkingOptionId": "default", "featureValues": {"x": True}},
    ]

    def roles(self) -> dict:
        def pol(role: str, family: str, provider: str, probe: dict) -> dict:
            return {"role": role, "family": family, "provider_id": provider, "probe": probe}
        return {
            "supervisor": pol("supervisor", "claude", "slp-supervisor", {"model": "claude-haiku-5-5", "mode": "plan"}),
            "claude-checker": pol("claude-checker", "claude", "slp-claude-checker",
                                  {"model": "claude-haiku-5-5", "profile": ["slp-checker-sonnet", "slp-checker-opus"]}),
            "claude-advisor": pol("claude-advisor", "claude", "slp-claude-advisor",
                                  {"model": "claude-sonnet-5-5", "mode": "bypassPermissions", "thinking": "high",
                                   "profile": "slp-advisor-sonnet"}),
            "agy-builder": pol("agy-builder", "antigravity", "antigravity",
                               {"model": "gemini-4-argon-medium", "mode": None, "profile": "slp-builder-flash"}),
            "pi-builder": pol("pi-builder", "pi", "slp-pi-builder", {"model": "pi-m", "mode": None}),  # no probe.profile
            "opencode-builder": pol("opencode-builder", "opencode", "slp-opencode-builder",
                                    {"model": "x", "mode": "slp-builder", "skip_reason": "quota"}),
        }

    def test_one_variant_per_profile_for_every_family(self) -> None:
        roles = self.roles()
        before = copy.deepcopy(roles)
        v = self.conf.apply_profile_models(roles, self.PROFILES, None)
        self.assertEqual(roles, before)  # input untouched
        self.assertEqual(set(v), {"supervisor", "claude-checker@slp-checker-sonnet", "claude-checker@slp-checker-opus",
                                  "claude-advisor", "agy-builder", "pi-builder", "opencode-builder"})
        sup = v["supervisor"]["probe"]
        self.assertEqual((sup["model"], sup["mode"], sup["thinking"]), ("claude-opus-5-5", "bypassPermissions", "high"))
        son = v["claude-checker@slp-checker-sonnet"]["probe"]
        opus = v["claude-checker@slp-checker-opus"]["probe"]
        self.assertEqual((son["model"], son["thinking"], son["profile_id"]), ("claude-sonnet-5-5", "high", "slp-checker-sonnet"))
        self.assertEqual((opus["model"], opus["thinking"], opus["profile_id"]), ("claude-opus-5-5", "medium", "slp-checker-opus"))
        adv = v["claude-advisor"]["probe"]
        self.assertEqual((adv["model"], adv["thinking"], adv["mode"], adv["profile_id"]),
                         ("claude-sonnet-5-5", "high", "bypassPermissions", "slp-advisor-sonnet"))
        agy = v["agy-builder"]["probe"]
        self.assertEqual((agy["model"], agy["mode"]), ("gemini-3.8-flash-high", "full-access"))
        self.assertNotIn("thinking", agy)
        pi = v["pi-builder"]["probe"]  # unmapped role: falls back to the single profile on its provider
        self.assertEqual((pi["model"], pi["profile_id"]), ("openai-api/gemini-3.8-flash-high", "slp-builder-pi-flash"))
        oc = v["opencode-builder"]["probe"]
        self.assertEqual((oc["model"], oc["mode"], oc["thinking"], oc["feature_values"]), ("opencode-go/deepseek-v4.1-flash", "slp-builder", "default", {"x": True}))
        self.assertEqual(oc["skip_reason"], "quota")  # kept: --ignore-skip removes it in main()
        self.assertEqual(roles["supervisor"]["probe"]["model"], "claude-haiku-5-5")

    def test_variant_labels(self) -> None:
        self.assertEqual(self.conf.variant_label("lead", {"id": "slp-lead"}, 1), "lead")
        self.assertEqual(self.conf.variant_label("claude-checker", {"id": "slp-checker-opus"}, 2), "claude-checker@slp-checker-opus")
        self.assertEqual(self.conf.tag_of("claude-checker@slp-checker-opus@tools"), "claude-checker-slp-checker-opus-tools")

    def test_thinking_override_is_claude_only(self) -> None:
        v = self.conf.apply_profile_models(self.roles(), self.PROFILES, "low")
        self.assertEqual(self.conf.thinking_args(v["supervisor"]), ["--thinking", "low"])
        self.assertEqual(self.conf.thinking_args(v["claude-checker@slp-checker-opus"]), ["--thinking", "low"])
        self.assertEqual(self.conf.thinking_args(v["claude-advisor"]), ["--thinking", "low"])
        self.assertEqual(self.conf.thinking_args(v["opencode-builder"]), ["--thinking", "default"])
        self.assertEqual(self.conf.thinking_args(v["agy-builder"]), [])

    def test_missing_or_foreign_profile_exits(self) -> None:
        with self.assertRaises(SystemExit):
            self.conf.apply_profile_models(self.roles(), [], None)
        roles = {"claude-advisor": self.roles()["claude-advisor"]}
        bad = [dict(self.PROFILES[3], provider="slp-claude-checker")]  # right id, wrong provider
        with self.assertRaises(SystemExit):
            self.conf.apply_profile_models(roles, bad, None)
        roles["claude-advisor"]["probe"]["profile"] = ["slp-advisor-sonnet", "slp-gone"]  # one id missing
        with self.assertRaises(SystemExit):
            self.conf.apply_profile_models(roles, self.PROFILES, None)

    def test_every_real_role_maps_to_live_style_profile(self) -> None:
        """Every roles/*/policy.yaml names a profile id (probe.profile) so the mapping is explicit, not guessed."""
        for role, pol in self.conf.load_roles().items():
            ids = self.conf.profile_ids(pol)
            self.assertTrue(ids, f"{role}: probe.profile missing")
            self.assertTrue(all(i.startswith("slp-") for i in ids), role)


if __name__ == "__main__":
    unittest.main()
