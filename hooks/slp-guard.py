#!/usr/bin/env python3
"""SLP guard: Claude Code PreToolUse / SessionStart hook driven by a per-role policy.

Usage (wired by slp-build into build/claude/<role>.settings.json):
  slp-guard.py --role <role>                 PreToolUse: reads hook JSON on stdin; exit 2 blocks
  slp-guard.py --role <role> --session-start print role.md as additionalContext (prompt fallback)

Policy comes from build/claude/<role>.policy.json (generated; stdlib-only here).
Fails CLOSED: any unexpected error blocks the tool call (exit 2).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLP_BIN = ROOT / "bin"
# absolute paths of these tools are normalized to the bare name
SLP_TOOLS = ("slp-verify", "slp-evidence")
SYSTEM_DIRS = ("/bin/", "/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/")
OPS = {";", "&&", "||", "|", "&", "\n"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
WRAPPERS = {"sudo", "env", "command", "nohup", "time", "exec", "xargs", "nice", "builtin"}
GIT_ARG_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


class Block(Exception):
    """Raised to block the tool call with a human-readable reason."""


def load_policy(role: str) -> dict:
    path = ROOT / "build" / "claude" / f"{role}.policy.json"
    return json.loads(path.read_text())


def deny(reason: str) -> None:
    sys.stderr.write(f"SLP guard ({ROLE}): {reason}\n")
    sys.exit(2)


# ---------------------------------------------------------------- shell parsing
def tokenize(cmd: str) -> list[str]:
    """Split a shell command into words and operator tokens (quote aware)."""
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def split_segments(tokens: list[str], strict: bool) -> list[list[str]]:
    segs: list[list[str]] = [[]]
    for tok in tokens:
        if tok in OPS or (not strict and tok and set(tok) <= set("()")):
            segs.append([])
        elif tok and set(tok) <= set("();&|<>"):
            if strict:
                raise Block(f"unsupported shell operator {tok!r}")
            # permissive roles: drop redirection operators, keep scanning the rest
        else:
            segs[-1].append(tok)
    return [s for s in segs if s]


def norm_cmd(tok: str) -> str:
    if "/" not in tok:
        return tok
    for tool in SLP_TOOLS:
        if tok == str(SLP_BIN / tool):
            return tool
    if tok.startswith(SYSTEM_DIRS):
        return tok.rsplit("/", 1)[1]
    return tok  # unknown path: will not match any allowlist


def strip_leading(seg: list[str], strip_wrappers: bool) -> list[str]:
    words = list(seg)
    while strip_wrappers and words and (
        norm_cmd(words[0]) in WRAPPERS or re.fullmatch(r"[A-Za-z_]\w*=.*", words[0])
    ):
        words.pop(0)
        while words and words[0].startswith("-") and words[0] != "--":
            words.pop(0)
    return words


def normalize(seg: list[str], strip_wrappers: bool) -> str:
    """Return 'cmd args...' with git global options removed."""
    words = strip_leading(seg, strip_wrappers)
    if not words:
        return ""
    cmd = norm_cmd(words[0])
    rest = words[1:]
    if cmd == "git":
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in GIT_ARG_OPTS else 1
        rest = rest[i:]
    return " ".join([cmd, *rest])


def git_effective_cwd(seg: list[str], cwd: str) -> str:
    """Directory a `git [-C dir]...` segment really runs in (so branch checks look at the right HEAD).

    `--git-dir` / `--work-tree` could point the command at another checkout behind our back: refuse them.
    """
    words = strip_leading(seg, True)
    if not words or norm_cmd(words[0]) != "git":
        return cwd
    eff = cwd or "."
    rest = words[1:]
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        opt = rest[i]
        if opt.split("=", 1)[0] in {"--git-dir", "--work-tree"}:
            raise Block(f"git {opt.split('=', 1)[0]} is not allowed")
        if opt == "-C":
            if i + 1 >= len(rest):
                raise Block("git -C without a directory")
            eff = os.path.join(eff, rest[i + 1])
            i += 2
        else:
            i += 2 if opt in GIT_ARG_OPTS else 1
    return eff


def inner_commands(seg: list[str]) -> list[str]:
    """Commands hidden in `sh -c '...'`, `eval ...`, `$(...)` and backticks."""
    out: list[str] = []
    words = list(seg)
    while words and (norm_cmd(words[0]) in WRAPPERS or "=" in words[0]):
        words.pop(0)
    if not words:
        return out
    cmd = norm_cmd(words[0])
    if cmd in {"bash", "sh", "zsh", "dash"} and "-c" in words:
        idx = words.index("-c")
        if idx + 1 < len(words):
            out.append(words[idx + 1])
    elif cmd == "eval":
        out.append(" ".join(words[1:]))
    for word in words:
        out += re.findall(r"\$\(([^()]*)\)", word)
        out += re.findall(r"`([^`]*)`", word)
    return out


def git_info(norm: str) -> tuple[str, str]:
    parts = norm.split(" ", 2)
    if parts[0] != "git":
        return "", ""
    return (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")


def current_branch(cwd: str) -> str:
    for args in (["symbolic-ref", "-q", "--short", "HEAD"], ["rev-parse", "--abbrev-ref", "HEAD"]):
        res = subprocess.run(
            ["git", "-C", cwd or ".", *args], capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    raise Block("cannot determine current git branch (fail closed)")


def check_git_protection(norm: str, policy: dict, cwd: str) -> None:
    protected = policy.get("protected_branches") or []
    sub, rest = git_info(norm)
    if not protected or not sub:
        return
    words = re.split(r"\s+", rest)
    if sub in {"merge", "rebase", "checkout", "switch", "pull"} and any(
        w in protected for w in words
    ):
        raise Block(f"`git {sub}` involving protected branch {protected} is forbidden")
    if sub in {"merge", "rebase", "pull"} and current_branch(cwd) in protected:
        raise Block(f"`git {sub}` while HEAD is on a protected branch {protected} is forbidden")


def check_segment(norm: str, policy: dict, cwd: str) -> None:  # cwd = effective git directory
    mode = policy["bash"]
    if not norm:
        return
    for pat in policy.get("bash_deny") or []:
        if re.search(pat, norm):
            raise Block(f"command matches deny pattern {pat!r}: {norm[:120]}")
    check_git_protection(norm, policy, cwd)
    if mode in {"allowlist", "readonly"}:
        if not any(re.search(p, norm) for p in policy.get("bash_allow") or []):
            raise Block(f"command not in the {mode} allowlist: {norm[:120]}")


def check_bash(command: str, policy: dict, cwd: str, depth: int = 0) -> None:
    mode = policy["bash"]
    if mode == "none":
        raise Block("Bash is not allowed for this role")
    if depth > 3:
        raise Block("command nesting too deep")
    strict = mode in {"allowlist", "readonly"}
    if strict:
        probe = re.sub(r"\s2>\s*/dev/null|\s2>&1", " ", command)
        if re.search(r"[<>`]|\$\(|\$\{|\n|\r", probe):
            raise Block("redirections, substitutions and multi-line commands are not allowed")
        command = probe
    try:
        segs = split_segments(tokenize(command), strict)
    except (ValueError, Block) as exc:
        if strict:
            raise Block(f"cannot parse command ({exc}); fail closed")
        # permissive roles: fall back to raw-string deny patterns
        for pat in policy.get("bash_deny") or []:
            if re.search(pat, command):
                raise Block(f"command matches deny pattern {pat!r}")
        return
    for seg in segs:
        # WHY: strict (allowlist/readonly) roles must not get `VAR=x slp-verify`, `env`, `sudo`... normalized away:
        # an env prefix could redirect a trusted tool (e.g. SLP_REPOS_DIR). Permissive roles strip wrappers so
        # `sudo git push` still hits the deny patterns.
        norm = normalize(seg, strip_wrappers=not strict)
        check_segment(norm, policy, git_effective_cwd(seg, cwd) if norm.startswith("git ") else cwd)
        if not strict:
            for inner in inner_commands(seg):
                check_bash(inner, policy, cwd, depth + 1)
        elif inner_commands(seg):
            raise Block("nested shell commands are not allowed")


# ---------------------------------------------------------------- file writes
def _git_out(directory: Path, *args: str) -> str | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    res = subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=10, env=env
    )
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None


def common_dir(directory: Path) -> Path | None:
    """Realpath of `git rev-parse --path-format=absolute --git-common-dir` for a directory, or None."""
    if not directory.is_dir():
        return None
    out = _git_out(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(os.path.realpath(out)) if out else None


def in_repo_slp(target: Path, anchors: list[str]) -> bool:
    """True when `target` (already realpath'd) is under `<toplevel>/.slp` of ANY worktree that shares the
    git common dir with one of the anchor directories (cwd, CLAUDE_PROJECT_DIR)."""
    anchor_commons = {c for a in anchors if a and (c := common_dir(Path(os.path.realpath(a))))}
    if not anchor_commons:
        return False
    probe = target.parent
    while not probe.exists() and probe != probe.parent:  # nearest existing ancestor
        probe = probe.parent
    if not probe.is_dir() or common_dir(probe) not in anchor_commons:
        return False
    top = _git_out(probe, "rev-parse", "--show-toplevel")
    if not top:
        return False
    return (Path(os.path.realpath(top)) / ".slp") in target.parents


def check_write(tool: str, tool_input: dict, policy: dict, cwd: str) -> None:
    mode = policy["write"]
    if mode == "all":
        return
    if mode == "none":
        raise Block(f"{tool} is not allowed for this role (read-only)")
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if tool == "NotebookEdit" or not path:
        raise Block(f"{tool} is not allowed for this role")
    # realpath resolves symlinks and `..`, so neither can escape the allowed roots
    base = Path(os.path.realpath(cwd))
    target = Path(os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path)))
    roots = [base / ".slp"]
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        roots.append(Path(os.path.realpath(proj)) / ".slp")
    if any(r in target.parents for r in roots):
        return
    if policy.get("slp_scope") == "repo" and in_repo_slp(target, [cwd, proj or ""]):
        return
    scope = "of any worktree of this repository" if policy.get("slp_scope") == "repo" else "under the workspace"
    raise Block(f"writes are limited to .slp/ {scope}; refused: {path} (cwd={cwd})")


# ---------------------------------------------------------------- entry points
def session_start(role: str) -> None:
    text = (ROOT / "roles" / role / "role.md").read_text().replace("<SLP_ROOT>", str(ROOT))
    lines = text.split("\n", 1)
    # Distinct marker so conformance can tell hook delivery from --append-system-prompt delivery.
    lines[0] = lines[0].replace("ROLE-MARKER:", "ROLE-MARKER-HOOK:")
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }
    print(json.dumps(payload))


def main() -> None:
    global ROLE
    args = sys.argv[1:]
    if "--role" not in args:
        ROLE = "?"
        deny("missing --role")
    ROLE = args[args.index("--role") + 1]
    if "--session-start" in args:
        session_start(ROLE)
        return
    try:
        policy = load_policy(ROLE)
        data = json.load(sys.stdin)
        tool = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        cwd = data.get("cwd") or os.getcwd()
        if tool == "Bash":
            check_bash(tool_input.get("command", ""), policy, cwd)
        elif tool in WRITE_TOOLS:
            check_write(tool, tool_input, policy, cwd)
    except Block as exc:
        deny(str(exc))
    except Exception as exc:  # fail closed
        deny(f"internal error, blocking (fail closed): {type(exc).__name__}: {exc}")


ROLE = "?"

if __name__ == "__main__":
    main()
