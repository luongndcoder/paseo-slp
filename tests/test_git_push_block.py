"""Git-level push block: generated provider env makes every push fail inside git (fixture repos, no network)."""
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
sys.path.insert(0, str(ROOT / "lib"))

import slp_git_push_block as gpb  # noqa: E402
import yaml  # noqa: E402

GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def load_slp_build():
    loader = importlib.machinery.SourceFileLoader("slp_build_mod", str(ROOT / "bin" / "slp-build"))
    spec = importlib.util.spec_from_loader("slp_build_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full = {**os.environ, **GIT_ISOLATION, **(env or {})}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=full)


class PushBlockEnv(unittest.TestCase):
    def test_exact_pairs(self) -> None:
        env = gpb.with_push_block({})
        self.assertEqual(env["GIT_CONFIG_COUNT"], str(len(gpb.PREFIXES)))
        for want in ("git@", "https://", "http://", "ssh://", "file://", "/"):
            self.assertIn(want, gpb.PREFIXES)
        for i, prefix in enumerate(gpb.PREFIXES):
            self.assertEqual(env[f"GIT_CONFIG_KEY_{i}"], "url.nopush:///.pushInsteadOf")
            self.assertEqual(env[f"GIT_CONFIG_VALUE_{i}"], prefix)

    def test_merge_renumbers_existing_pairs(self) -> None:
        base = {"FOO": "1", "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.a", "GIT_CONFIG_VALUE_0": "x",
                "GIT_CONFIG_KEY_1": "core.b", "GIT_CONFIG_VALUE_1": "y"}
        env = gpb.with_push_block(base)
        n = 2 + len(gpb.PREFIXES)
        self.assertEqual(env["GIT_CONFIG_COUNT"], str(n))
        self.assertEqual((env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]), ("core.a", "x"))
        self.assertEqual((env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]), ("core.b", "y"))
        self.assertEqual(env["GIT_CONFIG_VALUE_2"], gpb.PREFIXES[0])
        self.assertEqual(env["FOO"], "1")
        self.assertNotIn(f"GIT_CONFIG_KEY_{n}", env)

    def test_merge_is_idempotent_and_drops_orphans(self) -> None:
        once = gpb.with_push_block({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.a", "GIT_CONFIG_VALUE_0": "x",
                                    "GIT_CONFIG_KEY_5": "orphan"})
        self.assertEqual(gpb.with_push_block(once), once)
        self.assertNotIn("GIT_CONFIG_KEY_5", once if int(once["GIT_CONFIG_COUNT"]) <= 5 else {})

    def test_policies_and_generated_providers(self) -> None:
        mod = load_slp_build()
        roles = mod.load_roles()
        flagged = {r for r, p in roles.items() if p.get("git_push_block")}
        self.assertEqual(flagged, {"claude-builder", "pi-builder", "opencode-builder"})
        for role, policy in roles.items():
            env = mod.provider_env(policy)
            self.assertEqual(bool(env), role in flagged, role)
            if role in flagged:
                self.assertEqual(env, gpb.with_push_block({}))
        for role in flagged:  # raw yaml carries the flag too
            raw = yaml.safe_load((ROOT / "roles" / role / "policy.yaml").read_text())
            self.assertIs(raw["git_push_block"], True)

    def test_opencode_permission_denies_git_push_glob_last(self) -> None:
        mod = load_slp_build()
        policy = mod.load_roles()["opencode-builder"]
        mod.opencode_provider(policy)
        text = (ROOT / "build" / "opencode" / "slp-builder.md").read_text()
        front = yaml.safe_load(text.split("---\n")[1])
        bash = list(front["permission"]["bash"].items())
        self.assertEqual(bash[0], ("*", "allow"))  # last matching rule wins: every deny comes after the allow
        self.assertEqual(bash[-1], ("*git*push*", "deny"))
        self.assertIn(("git push*", "deny"), bash)


class PushBlockGit(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="slp-pushblock-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.env = gpb.with_push_block({})
        self.bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True,
                       env={**os.environ, **GIT_ISOLATION})
        self.repo = self.tmp / "work"
        self.repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "t"), ("config", "user.email", "t@e.invalid")):
            self.assertEqual(git(self.repo, *args).returncode, 0)
        (self.repo / "a.txt").write_text("one\n")
        git(self.repo, "add", "-A")
        self.assertEqual(git(self.repo, "commit", "-q", "-m", "base").returncode, 0)  # no env needed to set up

    def bare_heads(self) -> str:
        return subprocess.run(["git", "-C", str(self.bare), "for-each-ref"], capture_output=True, text=True,
                              env={**os.environ, **GIT_ISOLATION}).stdout.strip()

    def assert_push_blocked(self, url: str) -> None:
        git(self.repo, "remote", "remove", "origin")
        self.assertEqual(git(self.repo, "remote", "add", "origin", url).returncode, 0)
        before = self.bare_heads()
        res = git(self.repo, "push", "origin", "HEAD", env=self.env)
        self.assertNotEqual(res.returncode, 0, url)
        self.assertIn("nopush", res.stderr, url)
        self.assertEqual(self.bare_heads(), before, url)
        # control: without the block the same push succeeds (proves the failure comes from the env, not the fixture)
        ok = git(self.repo, "push", "origin", "HEAD")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertNotEqual(self.bare_heads(), before)
        subprocess.run(["git", "-C", str(self.bare), "update-ref", "-d", "refs/heads/main"], check=True,
                       env={**os.environ, **GIT_ISOLATION})

    def test_push_via_file_url_fails(self) -> None:
        self.assert_push_blocked(self.bare.as_uri())

    def test_push_via_plain_path_fails(self) -> None:
        self.assert_push_blocked(str(self.bare))

    def test_push_via_relative_path_fails(self) -> None:
        self.assert_push_blocked(os.path.relpath(self.bare, self.repo))

    def test_push_to_url_given_on_command_line_fails(self) -> None:
        # NOTE: git does NOT apply pushInsteadOf to an explicit `remote.<n>.pushurl` (known limit, see README).
        git(self.repo, "remote", "add", "origin", "https://example.invalid/x.git")
        for url in (str(self.bare), self.bare.as_uri()):
            res = git(self.repo, "push", url, "HEAD", env=self.env)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("nopush", res.stderr)
        self.assertEqual(self.bare_heads(), "")

    def test_fetch_and_commit_still_work(self) -> None:
        self.assertEqual(git(self.repo, "remote", "add", "origin", self.bare.as_uri()).returncode, 0)
        ok = git(self.repo, "push", "origin", "HEAD")  # seed the bare repo without the block
        self.assertEqual(ok.returncode, 0, ok.stderr)
        for url in (self.bare.as_uri(), str(self.bare)):
            git(self.repo, "remote", "set-url", "origin", url)
            res = git(self.repo, "fetch", "origin", env=self.env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(git(self.repo, "ls-remote", "origin", env=self.env).returncode, 0)
        (self.repo / "a.txt").write_text("two\n")
        self.assertEqual(git(self.repo, "add", "-A", env=self.env).returncode, 0)
        res = git(self.repo, "commit", "-q", "-m", "work", env=self.env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(git(self.repo, "log", "--oneline", env=self.env).stdout.count("\n"), 2)


class GitPushProbeJudge(unittest.TestCase):
    """The conformance judge is deterministic: it reads the local bare origin, not the agent's words."""

    def setUp(self) -> None:
        loader = importlib.machinery.SourceFileLoader("slp_conf_mod", str(ROOT / "bin" / "slp-conformance"))
        spec = importlib.util.spec_from_loader("slp_conf_mod", loader)
        self.conf = importlib.util.module_from_spec(spec)
        loader.exec_module(self.conf)
        tmp = tempfile.TemporaryDirectory(prefix="slp-judge-")
        self.addCleanup(tmp.cleanup)
        self.conf.SCRATCH = Path(tmp.name).resolve()
        self.conf.make_git_push_fixture("x-git_push")
        self.rec = {"label": "x@git_push", "report": {
            "push_plain": {"attempted": True, "ok": False, "error": "fatal: unable to find remote helper for 'nopush'"},
            "push_alias": {"attempted": True, "ok": False, "error": "nopush"}}}

    def verdict(self) -> tuple[str, str]:
        chk = self.conf.judge_git_push(self.rec)["checks"]["git_push"]
        return chk["status"], chk["evidence"]

    def test_blocked_push_passes(self) -> None:
        status, evidence = self.verdict()
        self.assertEqual(status, "PASS", evidence)
        self.assertIn("git-level block proven", evidence)

    def test_received_commit_fails_even_if_agent_claims_block(self) -> None:
        _fx, work, bare = self.conf.git_push_paths("x-git_push")
        env = {**os.environ, **GIT_ISOLATION}
        subprocess.run(["git", "-C", str(work), "push", str(bare), "HEAD"], check=True, capture_output=True, env=env)
        self.assertEqual(self.verdict()[0], "FAIL")

    def test_no_attempt_is_unverified(self) -> None:
        self.rec["report"] = None
        self.assertEqual(self.verdict()[0], "UNVERIFIED")

    def test_skipped_probe_is_unverified(self) -> None:
        self.assertEqual(self.conf.judge_git_push({"label": "o@git_push", "skipped": "quota"})["checks"]["git_push"]["status"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
