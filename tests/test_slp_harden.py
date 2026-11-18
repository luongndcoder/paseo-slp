"""Harness hardening: spawn whitelist, worker tool lockdown, stale provider removal, antigravity push block, role prompts."""
from __future__ import annotations

import fnmatch
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import slp_git_push_block as gpb  # noqa: E402
import slp_policy  # noqa: E402
import slp_probes as probes  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from helpers import locked_guard_tree  # noqa: E402

GUARD = ROOT / "hooks" / "slp-guard.py"
PEERS = ("claude-builder", "claude-checker", "claude-final-reviewer", "claude-integration-reviewer", "claude-advisor")
SUPERVISOR_ALLOW = ["slp-lead", "slp-claude-integration-reviewer", "slp-claude-advisor"]
LEAD_ALLOW = ["antigravity", "slp-opencode-builder", "slp-claude-builder", "slp-pi-builder", "slp-claude-checker",
              "slp-opencode-checker", "slp-claude-final-reviewer", "slp-claude-advisor"]  # fmt: skip


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    loader.exec_module(mod)
    return mod


guard = load("slp_guard_h", GUARD)
build = load("slp_build_h", ROOT / "bin" / "slp-build")
ROLES = build.load_roles()
SKILLS = build.all_skill_names()


def raw_policy(role: str) -> dict:
    return yaml.safe_load((ROOT / "roles" / role / "policy.yaml").read_text())


def gp(role: str) -> dict:
    return slp_policy.guard_policy(raw_policy(role))


# ---------------------------------------------------------------- 1. spawn whitelist
class SpawnGuardTests(unittest.TestCase):
    def ok(self, role: str, provider: object) -> None:
        guard.check_spawn("create_agent", {"provider": provider, "initialPrompt": "x"}, gp(role))

    def no(self, role: str, tool_input: object, op: str = "create_agent") -> None:
        with self.assertRaises(guard.Block, msg=f"{role} {op} {tool_input!r}"):
            guard.check_spawn(op, tool_input, gp(role))

    def test_allow_lists_are_exactly_the_specified_ones(self) -> None:
        self.assertEqual(raw_policy("supervisor")["spawn_allow"], SUPERVISOR_ALLOW)
        self.assertEqual(raw_policy("lead")["spawn_allow"], LEAD_ALLOW)
        for role in set(ROLES) - {"supervisor", "lead"}:
            self.assertNotIn("spawn_allow", raw_policy(role), role)
            self.assertNotIn("spawn_allow", gp(role), role)

    def test_allowed_providers_with_and_without_model(self) -> None:
        for role, allow in (("supervisor", SUPERVISOR_ALLOW), ("lead", LEAD_ALLOW)):
            for pid in allow:
                for value in (pid, f"{pid}/m", f"{pid}/openai-api/gemini-3.8-flash-high", f"  {pid}/m "):
                    with self.subTest(role=role, provider=value):
                        self.ok(role, value)

    def test_other_providers_are_blocked(self) -> None:
        for provider in ("claude", "claude/claude-opus-5-5", "codex/gpt-6-astra", "slp-codex-advisor/x", "slp-codex-checker/x",
                         "slp-pi-builder/x", "slp-lead-evil/x",
                         "SLP-LEAD/x", "zs-claude-main/x", "opencode/x", "slp-/x", "/slp-lead", "x/slp-lead"):  # fmt: skip
            with self.subTest(provider=provider):
                self.no("supervisor", {"provider": provider})
        self.no("lead", {"provider": "slp-lead/x"})  # lead may not spawn another lead
        self.no("lead", {"provider": "slp-claude-integration-reviewer/x"})
        self.no("lead", {"provider": "slp-supervisor/x"})
        for role in ("supervisor", "lead"):  # the removed Codex roles stay unspawnable
            self.no(role, {"provider": "slp-codex-advisor/gpt-6-astra"})
            self.no(role, {"provider": "slp-codex-checker/gpt-6-astra"})

    def test_unparsable_or_missing_provider_fails_closed(self) -> None:
        for tool_input in ({}, {"provider": None}, {"provider": ""}, {"provider": "  "}, {"provider": 5}, {"provider": ["slp-lead"]},
                           {"provider": {"id": "slp-lead"}}, {"prov": "slp-lead"}, None, [], "slp-lead", ["slp-lead"], 7):  # fmt: skip
            with self.subTest(tool_input=tool_input):
                self.no("supervisor", tool_input)
                self.no("lead", tool_input)

    def test_send_agent_prompt_mode_switch_blocked(self) -> None:
        for role in ("supervisor", "lead"):
            guard.check_spawn("send_agent_prompt", {"agentId": "a", "prompt": "go"}, gp(role))
            for mode in ("bypassPermissions", "plan"):
                with self.assertRaises(guard.Block):
                    guard.check_spawn(
                        "send_agent_prompt", {"agentId": "a", "prompt": "x", "sessionMode": mode}, gp(role)
                    )
            with self.assertRaises(guard.Block):
                guard.check_spawn("send_agent_prompt", "not-a-dict", gp(role))

    def test_update_agent_and_set_agent_mode_always_blocked(self) -> None:
        for role in ("supervisor", "lead"):
            for op in ("update_agent", "set_agent_mode"):
                self.no(role, {"provider": "slp-lead/x", "agentId": "a", "modeId": "plan"}, op)
                self.no(role, {}, op)

    def test_missing_allow_list_fails_closed(self) -> None:
        for policy in ({}, {"spawn_allow": []}, {"spawn_allow": "slp-lead"}, {"spawn_allow": None}):
            with self.assertRaises(guard.Block):
                guard.check_spawn("create_agent", {"provider": "slp-lead/x"}, policy)

    def test_tool_name_matching(self) -> None:
        for tool in ("mcp__paseo__create_agent", "mcp__paseo__update_agent", "mcp__paseo__set_agent_mode",
                     "mcp__paseo-2__create_agent"):  # fmt: skip
            self.assertTrue(guard.spawn_op(tool), tool)
        for tool in ("mcp__paseo__list_agents", "create_agent", "Bash", "Agent", "",
                     "mcp__paseo__create_agent_x"):  # fmt: skip
            self.assertEqual(guard.spawn_op(tool), "", tool)


class SpawnHookProcessTests(unittest.TestCase):
    """The real hook command line, JSON on stdin, exit code is the contract (2 = block).

    Runs in ENFORCE mode (SLP_GUARD_ENFORCE=1) against a copied hook tree carrying the locked spawn policies."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="slp-spawn-")
        cls.script = locked_guard_tree(Path(cls._tmp.name), ("supervisor", "lead"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_hook(self, role: str, stdin: str, enforce: bool = True) -> int:
        cmd = f"{sys.executable} {self.script} --role {role} || exit 2"
        env = {k: v for k, v in os.environ.items() if k != "SLP_GUARD_ENFORCE"}
        if enforce:
            env["SLP_GUARD_ENFORCE"] = "1"
        return subprocess.run(["sh", "-c", cmd], input=stdin, capture_output=True, text=True, env=env).returncode

    def payload(self, tool: str, **tool_input: object) -> str:
        return json.dumps({"tool_name": f"mcp__paseo__{tool}", "tool_input": tool_input})

    def test_exit_codes(self) -> None:
        for role, allow in (("supervisor", SUPERVISOR_ALLOW), ("lead", LEAD_ALLOW)):
            for pid in allow:
                self.assertEqual(self.run_hook(role, self.payload("create_agent", provider=f"{pid}/m")), 0, (role, pid))
            self.assertEqual(self.run_hook(role, self.payload("create_agent", provider="claude/claude-haiku-5-5")), 2)
            self.assertEqual(self.run_hook(role, self.payload("update_agent", agentId="a", name="n")), 2)
            self.assertEqual(self.run_hook(role, self.payload("set_agent_mode", agentId="a", modeId="plan")), 2)

    def test_default_allows_without_enforce(self) -> None:
        for role in ("supervisor", "lead"):
            self.assertEqual(self.run_hook(role, self.payload("update_agent", agentId="a", name="n"), enforce=False), 0)
            self.assertEqual(self.run_hook(role, "not json{", enforce=False), 0)

    def test_garbage_input_fails_closed(self) -> None:
        for stdin in ("", "not json{", "[]", "null", '"mcp__paseo__create_agent"', '{"tool_name": 5}',
                      '{"tool_name": "mcp__paseo__create_agent"}',
                      '{"tool_name": "mcp__paseo__create_agent", "tool_input": "slp-lead"}'):  # fmt: skip
            with self.subTest(stdin=stdin):
                self.assertEqual(self.run_hook("supervisor", stdin), 2)
                self.assertEqual(self.run_hook("lead", stdin), 2)


class SpawnWiringTests(unittest.TestCase):
    def settings(self, role: str) -> dict:
        return build.claude_artifacts(ROLES[role], SKILLS)[0]

    def test_supervisor_and_lead_get_the_spawn_hook(self) -> None:
        for role in ("supervisor", "lead"):
            hooks = [(h["matcher"], h["hooks"][0]["command"]) for h in self.settings(role)["hooks"]["PreToolUse"]]
            spawn = [(m, c) for m, c in hooks if "mcp__paseo__" in m]
            self.assertEqual(len(spawn), 1, role)
            matcher, command = spawn[0]
            for tool in ("create_agent", "update_agent", "set_agent_mode"):
                self.assertRegex(f"mcp__paseo__{tool}", f"^(?:{matcher})$")
            for tool in ("list_agents", "create_workspace", "Bash"):
                self.assertNotRegex(f"mcp__paseo__{tool}", f"^(?:{matcher})$")
            self.assertTrue(command.endswith(f"--role {role} || exit 2"), command)  # fail closed
        self.assertEqual(probes.spawn_hook_commands(self.settings("lead")).keys(), set(probes.SPAWN_TOOLS))
        self.assertTrue(all(probes.spawn_hook_commands(self.settings("supervisor")).values()))

    def test_other_claude_roles_have_no_spawn_hook(self) -> None:
        for role in PEERS:
            matchers = [h["matcher"] for h in self.settings(role).get("hooks", {}).get("PreToolUse", [])]
            self.assertFalse(any("mcp__paseo__" in m for m in matchers), role)

    def test_guard_policy_carries_spawn_allow_only_for_boss_roles(self) -> None:
        self.assertEqual(build.claude_artifacts(ROLES["supervisor"], SKILLS)[1]["spawn_allow"], SUPERVISOR_ALLOW)
        self.assertEqual(build.claude_artifacts(ROLES["lead"], SKILLS)[1]["spawn_allow"], LEAD_ALLOW)
        self.assertNotIn("spawn_allow", build.claude_artifacts(ROLES["claude-checker"], SKILLS)[1])

    def test_spawn_allow_ids_are_known_provider_ids(self) -> None:
        known = {p["provider_id"] for p in ROLES.values()}
        for role in ("supervisor", "lead"):
            self.assertLessEqual(set(ROLES[role]["spawn_allow"]), known)

    def test_generate_rejects_unknown_spawn_id(self) -> None:
        roles = {r: dict(p) for r, p in ROLES.items()}
        roles["lead"]["spawn_allow"] = ["slp-lead", "slp-typo"]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build, "load_roles", return_value=roles), \
                mock.patch.object(build, "BUILD_DIR", Path(tmp)):  # fmt: skip
            with self.assertRaises(SystemExit) as cm:
                build.generate()
        self.assertIn("slp-typo", str(cm.exception))

    def test_load_roles_rejects_empty_spawn_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roles_dir = Path(tmp)
            (roles_dir / "x").mkdir()
            (roles_dir / "x" / "policy.yaml").write_text("role: x\nspawn_allow: []\n")
            (roles_dir / "x" / "role.md").write_text("ROLE-MARKER: slp-x\n")
            with mock.patch.object(build, "ROLES_DIR", roles_dir), self.assertRaises(SystemExit):
                build.load_roles()


# ---------------------------------------------------------------- 4. peers deny AskUserQuestion
class PeerDenyTests(unittest.TestCase):
    def deny(self, role: str) -> set[str]:
        return set(build.claude_artifacts(ROLES[role], SKILLS)[0]["permissions"]["deny"])

    def test_peers_deny_ask_user_and_exit_plan_mode(self) -> None:
        for role in PEERS:
            self.assertLessEqual({"AskUserQuestion", "ExitPlanMode"}, self.deny(role), role)
            self.assertLessEqual({"Agent", "Task", "NotebookEdit"}, self.deny(role), role)  # previous denies kept

    def test_supervisor_and_lead_keep_ask_user_question(self) -> None:
        for role in ("supervisor", "lead"):
            self.assertNotIn("AskUserQuestion", self.deny(role), role)
            self.assertNotIn("ExitPlanMode", self.deny(role), role)

    def test_ask_user_judge(self) -> None:
        settings = {"permissions": {"deny": ["AskUserQuestion", "ExitPlanMode"]}}
        good = {"tools_visible": ["Read", "Write", "Bash"], "ask_user": {"attempted": False, "ok": False, "error": "tool not present"}}
        self.assertEqual(probes.judge_ask_user(settings, good)["status"], "PASS")
        self.assertEqual(probes.judge_ask_user({"permissions": {"deny": ["Agent"]}}, good)["status"], "FAIL")
        self.assertEqual(probes.judge_ask_user(settings, {**good, "tools_visible": ["Read", "AskUserQuestion"]})["status"], "FAIL")
        self.assertEqual(probes.judge_ask_user(settings, {**good, "ask_user": {"attempted": True, "ok": True, "error": ""}})["status"], "FAIL")
        blocked = {**good, "ask_user": {"attempted": True, "ok": False, "error": "tool denied"}}
        self.assertEqual(probes.judge_ask_user(settings, blocked)["status"], "PASS")
        self.assertEqual(probes.judge_ask_user(settings, {**good, "ask_user": {"attempted": False, "ok": False, "error": ""}})["status"], "UNVERIFIED")
        self.assertEqual(probes.judge_ask_user(settings, None)["status"], "UNVERIFIED")


# ---------------------------------------------------------------- 3. pi tool lockdown
class PiToolTests(unittest.TestCase):
    EXCLUDED = ("subagent", "subagents_enable", "subagent_supervisor", "contact_supervisor", "bg_wait", "ask_user_question",
                "web_fetch", "web_search", "web_crawl", "web_screenshot", "memongo_search", "memongo_save", "memongo_status")  # fmt: skip
    KEPT = ("read", "write", "edit", "bash", "todo", "grep", "find", "ls", "mcp__paseo__create_agent", "mcp__paseo__list_agents")

    def command(self) -> list[str]:
        return build.pi_provider(ROLES["pi-builder"])["command"]

    def test_argv_has_exclude_tools_and_keeps_mcp(self) -> None:
        cmd = self.command()
        self.assertEqual(cmd[:3], ["pi", "--append-system-prompt", str(ROOT / "roles" / "pi-builder" / "role.md")])
        self.assertEqual(cmd.count("--exclude-tools"), 1)
        self.assertNotIn("--no-mcp", cmd)
        self.assertNotIn("--tools", cmd)
        self.assertEqual(cmd[cmd.index("--exclude-tools") + 1], ",".join(raw_policy("pi-builder")["pi_exclude_tools"]))

    def test_patterns_cover_exactly_the_unwanted_tools(self) -> None:
        patterns = self.command()[self.command().index("--exclude-tools") + 1].split(",")
        match = lambda name: any(fnmatch.fnmatchcase(name, p) for p in patterns)  # noqa: E731
        for name in self.EXCLUDED:
            self.assertTrue(match(name), name)
        for name in self.KEPT:
            self.assertFalse(match(name), name)

    def test_other_roles_untouched(self) -> None:
        self.assertEqual(build.pi_tool_args({"role": "x"}), [])
        with self.assertRaises(SystemExit):
            build.pi_tool_args({"role": "x", "pi_exclude_tools": ["a,b"]})

    def test_judge(self) -> None:
        policy = raw_policy("pi-builder")
        argv = self.command()
        good = {"tools_visible": ["read", "bash", "edit", "write", "todo"]}
        self.assertEqual(probes.judge_pi_tools(policy, argv, good)["status"], "PASS")
        self.assertEqual(probes.judge_pi_tools(policy, argv, None)["status"], "UNVERIFIED")
        self.assertEqual(probes.judge_pi_tools(policy, ["pi"], good)["status"], "FAIL")  # live argv lacks the flag
        self.assertEqual(probes.judge_pi_tools(policy, [*argv, "--no-mcp"], good)["status"], "FAIL")
        leak = {"tools_visible": ["read", "bash", "edit", "write", "subagent"]}
        self.assertEqual(probes.judge_pi_tools(policy, argv, leak)["status"], "FAIL")
        lost = {"tools_visible": ["read", "bash", "edit"]}
        self.assertEqual(probes.judge_pi_tools(policy, argv, lost)["status"], "FAIL")
        self.assertEqual(probes.judge_pi_tools(policy, None, good)["status"], "FAIL")


# ---------------------------------------------------------------- 2. antigravity push block
class AntigravityEnvTests(unittest.TestCase):
    def test_policy_flags_the_block_and_env_is_the_push_block(self) -> None:
        policy = ROLES["agy-builder"]
        # 2026-11-18 owner decision: no push block; env is only the soft gitflow guard + force-push escape
        self.assertFalse(policy.get("git_push_block"))
        soft = {"MOBIO_GITFLOW_GUARD_MODE": "soft", "MOBIO_ALLOW_FORCE_PUSH": "1"}
        self.assertEqual(build.provider_env(policy), soft)
        self.assertEqual(build.add_env({"command": ["agy"]}, policy)["env"], soft)
        # the push-block helper itself still works when a (fixture) policy opts in
        self.assertEqual(build.provider_env({"git_push_block": True}), gpb.with_push_block({}))
        self.assertIn("env", build.OVERRIDE_FIELDS)

    def test_merge_override_env_keeps_foreign_keys_and_is_idempotent(self) -> None:
        wanted = gpb.with_push_block({})
        existing = {"KEEP": "1", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.a", "GIT_CONFIG_VALUE_0": "x"}
        merged = build.merge_override_env(existing, wanted)
        self.assertEqual(merged["KEEP"], "1")
        self.assertEqual((merged["GIT_CONFIG_KEY_0"], merged["GIT_CONFIG_VALUE_0"]), ("core.a", "x"))
        self.assertEqual(merged["GIT_CONFIG_COUNT"], str(1 + len(gpb.PREFIXES)))
        self.assertEqual(build.merge_override_env(merged, wanted), merged)
        self.assertEqual(build.merge_override_env(None, wanted), wanted)

    def test_merged_config_updates_only_owned_fields_of_the_override(self) -> None:
        cfg = {"agents": {"providers": {
            "antigravity": {"command": ["agy"], "paseoTools": {"enabled": True}, "label": "x", "extra": {"keep": True},
                            "env": {"FOO": "bar"}},
            "other": {"command": ["z"]},
        }}}  # fmt: skip
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg))
            providers = {"antigravity": {"command": ["agy", "--agent", "slp-agy-builder"], "paseoTools": {"enabled": False},
                                         "env": gpb.with_push_block({})}}  # fmt: skip
            with mock.patch.object(build, "CONFIG", path):
                _, new, _ = build.merged_config(providers)
                path.write_text(new)
                _, again, _ = build.merged_config(providers)
        live = json.loads(new)["agents"]["providers"]
        agy = live["antigravity"]
        self.assertEqual(agy["command"], ["agy", "--agent", "slp-agy-builder"])
        self.assertEqual(agy["extra"], {"keep": True})
        self.assertEqual(agy["env"]["FOO"], "bar")
        self.assertEqual(agy["env"]["GIT_CONFIG_COUNT"], "9")
        self.assertEqual(live["other"], {"command": ["z"]})
        self.assertEqual(again, new)  # idempotent

    def test_generated_override_in_providers_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build, "BUILD_DIR", Path(tmp)):
            providers = build.generate()
        soft = {"MOBIO_GITFLOW_GUARD_MODE": "soft", "MOBIO_ALLOW_FORCE_PUSH": "1"}
        for pid in ("antigravity", "slp-pi-builder", "slp-claude-checker"):
            self.assertEqual(providers[pid]["env"], soft, pid)
            self.assertNotIn("GIT_CONFIG_COUNT", providers[pid]["env"], pid)


# ---------------------------------------------------------------- claude-advisor (replaces the Codex roles)
class AdvisorRoleTests(unittest.TestCase):
    def test_no_codex_roles_or_family_left(self) -> None:
        self.assertNotIn("codex-checker", ROLES)
        self.assertNotIn("codex-advisor", ROLES)
        self.assertEqual({p["family"] for p in ROLES.values()}, {"claude", "opencode", "pi", "antigravity"})
        self.assertFalse(hasattr(build, "codex_provider"))
        self.assertFalse((ROOT / "build" / "codex").exists())

    def test_policy_shape(self) -> None:
        pol = ROLES["claude-advisor"]
        self.assertEqual((pol["family"], pol["provider_id"]), ("claude", "slp-claude-advisor"))
        self.assertEqual((pol["write"], pol["bash"]), ("all", "all-except"))  # opened 2026-11-18
        self.assertEqual((pol["bash_allow"], pol["bash_deny"], pol["protected_branches"]), ([], [], []))
        self.assertEqual(pol["paseo_tools"], {"enabled": False, "disabled": []})
        self.assertEqual(pol["skills_auto"], [])
        self.assertEqual(pol["probe"]["profile"], "slp-advisor-sonnet")
        self.assertEqual((pol["probe"]["model"], pol["probe"]["mode"]), ("claude-sonnet-5-5", "bypassPermissions"))
        self.assertNotIn("spawn_allow", pol)

    def test_artifacts_deny_every_write_and_ask_tool_and_have_no_write_hook(self) -> None:
        settings, guard_pol, provider = build.claude_artifacts(ROLES["claude-advisor"], SKILLS)
        deny = set(settings["permissions"]["deny"])
        # 2026-11-18: Edit/Write/MultiEdit are no longer denied (write: all); worker-only tools stay denied
        self.assertLessEqual({"NotebookEdit", "Agent", "Task", "AskUserQuestion", "ExitPlanMode"}, deny)
        self.assertTrue({"Edit", "Write", "MultiEdit"}.isdisjoint(deny))
        self.assertNotIn("Bash", deny)
        matchers = [h["matcher"] for h in settings["hooks"]["PreToolUse"]]
        self.assertEqual(matchers, ["Bash"])
        self.assertEqual((guard_pol["write"], guard_pol["bash"]), ("all", "all-except"))
        self.assertTrue(all(v == "off" for v in settings["skillOverrides"].values()))
        self.assertEqual(provider["extends"], "claude")
        self.assertEqual(provider["paseoTools"], {"enabled": False})
        self.assertEqual(provider["command"][0], str(ROOT / "bin" / "slp-claude"))

    def test_advisor_stance_is_not_debate_rewarding(self) -> None:
        text = re.sub(r"\s+", " ", (ROOT / "roles" / "claude-advisor" / "role.md").read_text())
        for needle in ("Challenge a premise ONLY when evidence warrants it", "\"No objection, proceed\" is a valid and expected answer",
                       "Never argue for its own sake"):  # fmt: skip
            self.assertIn(needle, text)
        self.assertNotIn("strongest risks", text)

    def test_role_md_mission_matches_the_old_advisor(self) -> None:
        text = re.sub(r"\s+", " ", (ROOT / "roles" / "claude-advisor" / "role.md").read_text())
        for needle in ("independent second opinion", "<= 40 lines of plain text", "`recommendation`, `risks`, `unknowns`, `evidence`",
                       "quotes real file:line text", "list it under `unknowns`", "Implement the solution"):  # fmt: skip
            self.assertIn(needle, text)


# ---------------------------------------------------------------- policy `models`
class ProviderModelsTests(unittest.TestCase):
    def test_opencode_policy_models_exact(self) -> None:
        self.assertEqual(
            ROLES["opencode-builder"]["models"],
            [{"id": "opencode-go/deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash (Go) \u2014 FE + BE", "isDefault": True}],
        )  # fmt: skip
        self.assertEqual(
            ROLES["opencode-checker"]["models"],
            [{"id": "opencode-go/deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash (Go) \u2014 checker", "isDefault": True}],
        )  # fmt: skip

    def test_models_reach_the_generated_provider_only_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build, "BUILD_DIR", Path(tmp)):
            providers = build.generate()
        # OpenCode gets `additionalModels`: a `models` list would make Paseo skip runtime mode discovery
        self.assertEqual(providers["slp-opencode-builder"]["additionalModels"], ROLES["opencode-builder"]["models"])
        self.assertTrue(sum(1 for m in providers["slp-opencode-builder"]["additionalModels"] if m.get("isDefault")) == 1)
        self.assertNotIn("models", providers["slp-opencode-builder"])
        self.assertNotIn("models", providers["slp-opencode-checker"])
        self.assertNotIn("models", providers["slp-claude-checker"])
        self.assertNotIn("models", providers["slp-pi-builder"])

    def test_add_models_copies_and_validates(self) -> None:
        pol = {"role": "x", "family": "claude", "models": [{"id": "a/b", "label": "B", "isDefault": True}]}
        out = build.add_models({"extends": "claude"}, pol)
        self.assertEqual(out["models"], pol["models"])
        self.assertIsNot(out["models"][0], pol["models"][0])  # copied, not aliased
        oc = build.add_models({"extends": "opencode"}, {**pol, "family": "opencode"})
        self.assertEqual(oc["additionalModels"], pol["models"])
        self.assertNotIn("models", oc)
        self.assertEqual(build.add_models({"k": 1}, {"role": "x"}), {"k": 1})
        for bad in ([], "a", [{"id": "a"}], [{"id": "a", "label": "A", "extra": 1}],
                    [{"id": "a", "label": "A", "isDefault": True}, {"id": "b", "label": "B", "isDefault": True}]):  # fmt: skip
            with self.assertRaises(SystemExit):
                build.add_models({}, {"role": "x", "family": "claude", "models": bad})

    def test_models_survive_merge_into_live_config(self) -> None:
        models = [{"id": "a/b", "label": "B", "isDefault": True}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"agents": {"providers": {"slp-opencode-builder": {"models": [{"id": "old", "label": "Old"}]}}}}))
            with mock.patch.object(build, "CONFIG", path):
                _, new, _ = build.merged_config({"slp-opencode-builder": {"extends": "opencode", "models": models}}, False)
        self.assertEqual(json.loads(new)["agents"]["providers"]["slp-opencode-builder"]["models"], models)


# ---------------------------------------------------------------- stale slp-* provider removal
class StaleProviderTests(unittest.TestCase):
    def merge(self, live: dict, providers: dict, only_claude: bool = False) -> tuple[dict, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"agents": {"providers": live}, "daemon": {"x": 1}}))
            with mock.patch.object(build, "CONFIG", path):
                _, new, stale = build.merged_config(providers, only_claude)
        cfg = json.loads(new)
        self.assertEqual(cfg["daemon"], {"x": 1})
        return cfg["agents"]["providers"], stale

    LIVE = {
        "slp-lead": {"extends": "claude", "command": ["old"]},
        "slp-codex-advisor": {"extends": "codex"},
        "slp-codex-checker": {"extends": "codex"},
        "codex": {"x": 1}, "zs-claude-main": {"y": 2}, "be-codex-worker": {}, "antigravity": {"command": ["agy"]},
    }

    def test_removes_only_slp_keys_without_a_role(self) -> None:
        got, stale = self.merge(self.LIVE, {"slp-lead": {"extends": "claude", "command": ["new"]}})
        self.assertEqual(sorted(stale), ["slp-codex-advisor", "slp-codex-checker"])
        self.assertEqual(set(got), {"slp-lead", "codex", "zs-claude-main", "be-codex-worker", "antigravity"})
        self.assertEqual(got["slp-lead"]["command"], ["new"])
        self.assertEqual(got["antigravity"], {"command": ["agy"]})  # non-slp keys untouched

    def test_new_roles_are_added_and_idempotent(self) -> None:
        wanted = {"slp-lead": {"extends": "claude"}, "slp-claude-advisor": {"extends": "claude"}}
        first, _ = self.merge(self.LIVE, wanted)
        self.assertIn("slp-claude-advisor", first)
        again, stale = self.merge(first, wanted)
        self.assertEqual((again, stale), (first, []))

    def test_only_claude_removes_nothing(self) -> None:
        got, stale = self.merge(self.LIVE, {"slp-lead": {"extends": "claude"}}, only_claude=True)
        self.assertEqual(sorted(stale), ["slp-codex-advisor", "slp-codex-checker"])
        self.assertIn("slp-codex-advisor", got)
        self.assertIn("slp-codex-checker", got)

    def test_dry_run_reports_and_apply_logs_removed(self) -> None:
        text = (ROOT / "bin" / "slp-build").read_text()
        self.assertIn("removed' if args.apply else 'to be removed on --apply'", text)


# ---------------------------------------------------------------- spawn probe judges
class SpawnProbeJudgeTests(unittest.TestCase):
    def rep(self, **over: dict) -> dict:
        block = {"attempted": True, "ok": False, "error": "PreToolUse hook blocking error: SLP guard (lead): not allowed"}
        base = {"create_denied": block, "update_denied": block, "mode_denied": block,
                "create_allowed": {"attempted": True, "ok": True, "error": ""}, "notes": ""}  # fmt: skip
        return {**base, **over}

    def live(self, rep, children) -> str:
        return probes.judge_spawn_live(rep, children, "lead-spawn", "slp-claude-checker/claude-haiku-5-5")["status"]

    ALLOWED = {"name": "slp-conf-lead-spawn-allowed", "provider": "slp-claude-checker/claude-haiku-5-5"}

    def test_pass(self) -> None:
        self.assertEqual(self.live(self.rep(), [self.ALLOWED]), "PASS")

    def test_denied_child_existing_is_fail_whatever_the_agent_says(self) -> None:
        denied = {"name": "slp-conf-lead-spawn-denied", "provider": "claude/claude-haiku-5-5"}
        self.assertEqual(self.live(self.rep(), [self.ALLOWED, denied]), "FAIL")
        self.assertEqual(self.live(None, [denied]), "FAIL")

    def test_block_text_must_come_from_the_guard(self) -> None:
        paraphrase = {"attempted": True, "ok": False, "error": "agent not found"}  # hook did NOT fire
        self.assertEqual(self.live(self.rep(update_denied=paraphrase), [self.ALLOWED]), "FAIL")
        self.assertEqual(self.live(self.rep(create_denied={**paraphrase, "ok": True}), [self.ALLOWED]), "FAIL")

    def test_allowed_spawn_blocked_by_guard_is_fail(self) -> None:
        refused = {"attempted": True, "ok": False, "error": "SLP guard (lead): provider not allowed"}
        self.assertEqual(self.live(self.rep(create_allowed=refused), []), "FAIL")

    def test_allowed_spawn_refused_by_paseo_is_unverified_not_fail(self) -> None:
        paseo = {"attempted": True, "ok": False, "error": "cannot inherit mode 'bypassPermissions' from caller"}
        self.assertEqual(self.live(self.rep(create_allowed=paseo), []), "UNVERIFIED")

    def test_wrong_provider_child_and_missing_report(self) -> None:
        self.assertEqual(self.live(self.rep(), [{**self.ALLOWED, "provider": "claude/x"}]), "FAIL")
        self.assertEqual(self.live(None, []), "UNVERIFIED")
        skipped = {"attempted": False, "ok": False, "error": "tool not present"}
        self.assertEqual(self.live(self.rep(create_denied=skipped), [self.ALLOWED]), "UNVERIFIED")

    def test_hook_judge_on_real_settings(self) -> None:
        if not (ROOT / "build/claude/lead.policy.json").exists():
            self.skipTest("run bin/slp-build first")
        settings = build.claude_artifacts(ROLES["lead"], SKILLS)[0]
        # the judge probes deny cases, which only exist in ENFORCE mode (default is always-allow since 2026-11-18)
        self.assertEqual(probes.judge_spawn_hook(settings, LEAD_ALLOW)["status"], "FAIL")
        with mock.patch.dict(os.environ, {"SLP_GUARD_ENFORCE": "1"}):
            self.assertEqual(probes.judge_spawn_hook(settings, LEAD_ALLOW)["status"], "PASS")
        settings["hooks"]["PreToolUse"] = [h for h in settings["hooks"]["PreToolUse"] if "mcp__paseo__" not in h["matcher"]]
        self.assertEqual(probes.judge_spawn_hook(settings, LEAD_ALLOW)["status"], "FAIL")

    def test_playbook_profiles_stay_inside_the_allow_lists(self) -> None:
        """Every provider the slp-lead / slp-supervisor playbooks tell the boss to spawn is whitelisted for that boss."""
        rows = re.findall(r"^\| (slp-[\w-]+) \| `([^`/]+)/", (ROOT / "skills/slp-lead/references/profiles.md").read_text(), re.M)
        self.assertGreaterEqual(len(rows), 10)
        supervisor_spawns = {"slp-lead", "slp-claude-integration-reviewer", "slp-claude-advisor"}
        for _profile, provider in rows:
            if provider in supervisor_spawns:
                self.assertIn(provider, SUPERVISOR_ALLOW)
            else:
                self.assertIn(provider, LEAD_ALLOW, provider)
        self.assertIn("slp-claude-advisor", SUPERVISOR_ALLOW)
        self.assertIn("slp-claude-advisor", LEAD_ALLOW)
        self.assertNotRegex((ROOT / "skills/slp-lead/references/profiles.md").read_text(), r"(?im)^\| slp-[\w-]*codex")

    def test_prompts_name_the_exact_tools(self) -> None:
        text = probes.spawn_prompt("lead-spawn", "slp-claude-checker/claude-haiku-5-5")
        for needle in ("create_agent", "update_agent", "set_agent_mode", "slp-conf-lead-spawn-denied", "slp-conf-lead-spawn-allowed",
                       probes.DENIED_PROVIDER, "bypassPermissions"):  # fmt: skip
            self.assertIn(needle, text)
        custom = probes.spawn_prompt("sup-spawn", "slp-claude-advisor/x", "slp-codex-advisor/x")
        self.assertIn("provider `slp-codex-advisor/x`", custom)
        self.assertNotIn(probes.DENIED_PROVIDER, custom)
        self.assertIn("AskUserQuestion", probes.tools_prompt(True))
        self.assertNotIn("AskUserQuestion", probes.tools_prompt(False))


# ---------------------------------------------------------------- 6. role prompts
class RolePromptTests(unittest.TestCase):
    def md(self, role: str) -> str:
        return (ROOT / "roles" / role / "role.md").read_text()

    def test_size_and_marker(self) -> None:
        for role in ROLES:
            text = self.md(role)
            self.assertLessEqual(len(text.splitlines()), 60, role)
            self.assertEqual(text.splitlines()[0], f"ROLE-MARKER: slp-{role}")

    def test_peer_precedence_block(self) -> None:
        for role in set(ROLES) - {"lead", "supervisor"}:
            text = self.md(role)
            self.assertIn("Precedence", text, role)
            for needle in ("You are a worker: never ask the user", "AskUserQuestion", "never index or scan", "index_repository",
                           "read only files the brief/diff needs"):  # fmt: skip
                self.assertIn(needle, re.sub(r"\s+", " ", text), (role, needle))

    def test_boss_precedence_block(self) -> None:
        for role in ("lead", "supervisor"):
            text = re.sub(r"\s+", " ", self.md(role))
            self.assertIn("Precedence", text)
            self.assertIn("Do not run index_repository on whole repos unless the task needs it; prefer targeted search.", text)
            self.assertNotIn("never ask the user (even", text)

    def test_builder_prompts_no_longer_claim_platform_rules_are_missing(self) -> None:
        for role in ("pi-builder", "agy-builder", "opencode-builder"):
            text = self.md(role)
            self.assertNotIn("do NOT reach you", text, role)
            self.assertIn("also reach you", text, role)
            self.assertIn("Tenant isolation", text, role)  # invariants stay inlined
            self.assertIn("Push, merge and force-push to any branch are allowed", text, role)
            self.assertNotIn("Never `git push`", text, role)

    DEBATE_REWARD = re.compile(r"devil|find holes|skeptical of the lead|challenge everything|poke holes|tear apart", re.I)

    def test_no_debate_reward_wording_in_roles_and_skills(self) -> None:
        files = list((ROOT / "roles").glob("*/role.md")) + list((ROOT / "skills").rglob("*.md"))
        self.assertGreaterEqual(len(files), 14)
        for path in files:
            text = path.read_text()
            if path.name == "prompts.md":  # the one place that tells the Lead NOT to ask for it
                text = text.replace('do\nnot ask it to "find holes"', "")
            self.assertIsNone(self.DEBATE_REWARD.search(text), (str(path), self.DEBATE_REWARD.search(text)))

    def test_no_boss_persona_lines(self) -> None:
        for path in [*(ROOT / "roles").glob("*/role.md"), *(ROOT / "skills").glob("*/SKILL.md")]:
            self.assertNotRegex(path.read_text(), r"(?i)\bthe boss\b|\byour boss\b|You are the boss", str(path))
        for role in ("supervisor", "lead"):
            text = re.sub(r"\s+", " ", self.md(role))
            for word in ("Responsibility:", "Authority:", "Escalate"):
                self.assertIn(word, text, role)

    def test_scope_rule_in_boss_prompts_and_skills(self) -> None:
        for rel in ("roles/supervisor/role.md", "roles/lead/role.md", "skills/slp-supervisor/SKILL.md", "skills/slp-lead/SKILL.md"):
            text = re.sub(r"\s+", " ", (ROOT / rel).read_text())
            for needle in ("small changes", "UI/UX feel", "slp-builder-deepseek", "slp-builder-sonnet"):
                self.assertIn(needle, text, (rel, needle))

    def test_builders_know_scope_challenge_and_three_part_brief(self) -> None:
        for role in ("claude-builder", "pi-builder", "agy-builder", "opencode-builder"):
            text = re.sub(r"\s+", " ", self.md(role))
            for needle in ("`goal`", "`constraints`", "`design_choice`", "scope_challenge", "never allows edits outside `files`"):
                self.assertIn(needle, text, (role, needle))
        lead = (ROOT / "skills/slp-lead/SKILL.md").read_text()
        self.assertIn("scope_challenge", lead)
        self.assertIn("EVALUATE it before", lead)

    def test_reviewers_pin_the_version_and_may_read_head(self) -> None:
        for role in ("claude-checker", "claude-final-reviewer", "claude-integration-reviewer"):
            text = self.md(role)
            self.assertIn("git rev-parse HEAD", text, role)
            self.assertIn("head_sha", text, role)
            self.assertIn("base_ref", text, role)
        for role in ("claude-checker", "claude-advisor", "claude-final-reviewer", "claude-integration-reviewer"):
            pol = raw_policy(role)
            allow = pol["bash_allow"]
            # 2026-11-18: bash is fully open (empty lists, all-except), so reading HEAD needs no allowlist entry
            self.assertEqual((pol["bash"], allow, pol["bash_deny"]), ("all-except", [], []), role)
        lead = (ROOT / "skills/slp-lead/SKILL.md").read_text()
        self.assertIn("valid ONLY for that commit", lead)
        self.assertIn("stale", lead)
        self.assertIn("metrics.reviews", lead)

    def test_generated_agent_files_carry_the_new_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build, "BUILD_DIR", Path(tmp)):
            md = build.antigravity_agent(ROLES["agy-builder"])
            self.assertIn("Precedence", md.read_text())
            self.assertNotIn("do NOT reach you", md.read_text())


if __name__ == "__main__":
    unittest.main()
