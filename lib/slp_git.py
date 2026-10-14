"""Small git helpers: changed files, diff lines, file content at a ref."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_SAFE = ["-c", "core.quotepath=false", "--no-pager"]
DIFF_FLAGS = ["--no-color", "--no-ext-diff", "--no-textconv", "--no-renames"]
MAX_UNTRACKED_BYTES = 1_000_000


class GitError(Exception):
    """A git command failed."""


@dataclass(frozen=True)
class DiffLine:
    path: str
    side: str  # "added" | "removed"
    lineno: int  # line number in the new file (added) or old file (removed)
    text: str


def git(worktree: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["git", *GIT_SAFE, "-C", str(worktree), *args], capture_output=True, text=True
    )
    if check and res.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {res.stderr.strip()[:300]}")
    return res.stdout if res.returncode == 0 else ""


def repo_name(worktree: Path) -> str:
    """Basename of the main repo (worktrees map to the repo they were created from)."""
    common = git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return Path(common).parent.name


def common_git_dir(path: Path) -> Path:
    """Absolute, symlink-resolved git common dir of the repository containing `path` (a directory)."""
    out = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return Path(out).resolve()


def ensure_excluded(worktree: Path, pattern: str = ".slp/") -> tuple[Path, bool]:
    """Idempotently append `pattern` to `<git common dir>/info/exclude`. Returns (file, changed)."""
    exclude = common_git_dir(worktree) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    if pattern in {ln.strip() for ln in text.splitlines()}:
        return exclude, False
    sep = "" if not text or text.endswith("\n") else "\n"
    exclude.write_text(text + sep + pattern + "\n")
    return exclude, True


def _z_list(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def untracked_files(worktree: Path) -> list[str]:
    return _z_list(git(worktree, "ls-files", "-z", "--others", "--exclude-standard"))


def changed_files(worktree: Path, base_ref: str) -> list[str]:
    """`git diff --name-only <base>...HEAD` + uncommitted tracked changes + untracked files."""
    names: set[str] = set()
    for rng in (f"{base_ref}...HEAD", "HEAD"):
        names.update(_z_list(git(worktree, "diff", "-z", "--name-only", *DIFF_FLAGS, rng)))
    names.update(untracked_files(worktree))
    return sorted(names)


def merge_base(worktree: Path, base_ref: str) -> str:
    return git(worktree, "merge-base", base_ref, "HEAD").strip() or base_ref


def parse_unified_diff(diff: str) -> list[DiffLine]:
    """Parse `git diff -U0` output into added/removed lines with line numbers."""
    out: list[DiffLine] = []
    old_path = new_path = ""
    old_no = new_no = 0
    for raw in diff.split("\n"):
        if raw.startswith("--- "):
            old_path = raw[4:].split("\t")[0]
            old_path = old_path[2:] if old_path.startswith("a/") else old_path
        elif raw.startswith("+++ "):
            new_path = raw[4:].split("\t")[0]
            new_path = new_path[2:] if new_path.startswith("b/") else new_path
        elif raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old_no, new_no = int(m.group(1)), int(m.group(2))
        elif raw.startswith("+") and not raw.startswith("+++"):
            path = new_path if new_path != "/dev/null" else old_path
            out.append(DiffLine(path, "added", new_no, raw[1:]))
            new_no += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            path = old_path if old_path != "/dev/null" else new_path
            out.append(DiffLine(path, "removed", old_no, raw[1:]))
            old_no += 1
    return out


def diff_lines(worktree: Path, base_ref: str) -> list[DiffLine]:
    """Added/removed lines of base...HEAD, uncommitted tracked changes and untracked files."""
    lines: list[DiffLine] = []
    for rng in (f"{base_ref}...HEAD", "HEAD"):
        lines += parse_unified_diff(git(worktree, "diff", "-U0", *DIFF_FLAGS, rng))
    for rel in untracked_files(worktree):
        path = worktree / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_UNTRACKED_BYTES:
                continue
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        lines += [DiffLine(rel, "added", n, t) for n, t in enumerate(text.splitlines(), 1)]
    return lines


def show(worktree: Path, ref: str, path: str) -> str | None:
    """File content at `ref`, or None if absent."""
    res = subprocess.run(
        ["git", *GIT_SAFE, "-C", str(worktree), "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return res.stdout if res.returncode == 0 else None
