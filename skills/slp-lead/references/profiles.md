# SLP profiles -> create_agent arguments

Source of truth is the live Paseo profile list (MCP `list_profiles`, snapshot 2026-10-23). There is no `profile`
parameter on `create_agent`: copy the fields below. If `list_profiles` disagrees with this file, the live list wins.

`create_agent` shape: `{title (<=60 chars), provider: "<provider>/<model>", initialPrompt, workspaceId?,
settings: {modeId?, thinkingOptionId?, features?}, notifyOnFinish: true}`.
Pass `workspaceId` = the workstream worktree's workspace (from `create_workspace`) for every Peer, so it runs in the
worktree and finds `.slp/` there; the Supervisor passes the repo's main workspace id for a Lead. Omit `settings` keys
the profile leaves empty. Never invent `modeId` for `slp-builder-pi-flash` (none on purpose).

| Profile id | provider (argument) | modeId | thinkingOptionId | Use |
|---|---|---|---|---|
| slp-lead | `slp-lead/claude-opus-5-5` | bypassPermissions | medium | Supervisor spawns one per workstream |
| slp-builder-deepseek | `slp-opencode-builder/opencode-go/deepseek-v4.1-flash` | slp-builder | default | FE AND BE builder, rounds 1-3; OpenCode Go quota |
| slp-builder-sonnet | `slp-claude-builder/claude-sonnet-5-5` | bypassPermissions | medium | rounds 4-5 and fallback for both FE and BE |
| slp-builder-pi-flash | `slp-pi-builder/openai-api/gemini-3.8-flash-high` | (none) | (none) | last-resort FE fallback #1 |
| slp-builder-flash | `antigravity/gemini-3.8-flash-high` | full-access | (none) | last-resort FE fallback #2 |
| slp-checker-sonnet | `slp-claude-checker/claude-sonnet-5-5` | bypassPermissions* | high | default checker (non-Claude builders); writes review.N.json |
| slp-checker-opus | `slp-claude-checker/claude-opus-5-5` | bypassPermissions* | medium | high-risk slices only; writes review.N.json |
| slp-checker-deepseek | `slp-opencode-checker/opencode-go/deepseek-v4.1-flash` | slp-checker | (none) | checker for SONNET builders (cross-family); read-only, CANNOT write: replies with ONE review JSON, you save it |
| slp-advisor-sonnet | `slp-claude-advisor/claude-sonnet-5-5` | bypassPermissions | high | design second opinion only (not a checker); plain text, writes nothing |
| slp-final-reviewer | `slp-claude-final-reviewer/claude-opus-5-5` | bypassPermissions* | high | whole workstream, once; writes final-review.json |
| slp-integration-reviewer | `slp-claude-integration-reviewer/claude-opus-5-5` | bypassPermissions* | medium | Supervisor only; writes integration-review.json |

\* Claude checker/reviewer providers now allow writes ONLY under `.slp/` (hook) with read-only bash, so the mode is
`bypassPermissions` (SLP-SPEC P3 decision 8). If the live `list_profiles` still says `plan`, the Write tool is refused:
the agent then replies with the JSON and you save it yourself.

Example (default builder):
```
create_agent(title="slp s1-export-service r1", provider="slp-opencode-builder/opencode-go/deepseek-v4.1-flash",
  workspaceId=<worktree workspace>, settings={modeId:"slp-builder"}, notifyOnFinish=true,
  initialPrompt=<builder prompt from prompts.md>)
```
Example (Claude checker, in the workstream worktree):
```
create_agent(title="slp check s1 r1", provider="slp-claude-checker/claude-sonnet-5-5", workspaceId=<worktree workspace>,
  settings={modeId:"bypassPermissions", thinkingOptionId:"high"}, notifyOnFinish=true, initialPrompt=<checker prompt>)
```
Example (DeepSeek checker for a Sonnet builder: cross-family; it cannot write, you save its reply):
```
create_agent(title="slp check s1 r1 deepseek", provider="slp-opencode-checker/opencode-go/deepseek-v4.1-flash", workspaceId=<worktree workspace>,
  settings={modeId:"slp-checker"}, notifyOnFinish=true, initialPrompt=<checker prompt>)
```
Example (Opus checker for a high-risk slice):
```
create_agent(title="slp check s1 r1 opus", provider="slp-claude-checker/claude-opus-5-5", workspaceId=<worktree workspace>,
  settings={modeId:"bypassPermissions", thinkingOptionId:"medium"}, notifyOnFinish=true, initialPrompt=<checker prompt>)
```
Example (advisor; Lead or Supervisor):
```
create_agent(title="slp advise <topic>", provider="slp-claude-advisor/claude-sonnet-5-5", workspaceId=<workspace>,
  settings={modeId:"bypassPermissions", thinkingOptionId:"high"}, notifyOnFinish=true, initialPrompt=<advisor prompt>)
```
Example (worktree for a workstream):
```
create_workspace(isolation="worktree", mode="branch-off", baseBranch="<base_branch>", branchName="slp/<task>-<ws>",
  worktreeSlug="<task>-<ws>", title="<task> <ws>", path="<main repo path>")   # then slp-verify --init <worktree>
```

## Known harness caveats (SLP-SPEC section 5, P1 log)
- `slp-builder-flash` runs on the built-in `antigravity` provider overridden to `agy --agent slp-agy-builder` (tool
  allowlist + Mobio invariants + git push block). It cannot path-limit writes: `slp-verify` scope/sandbox checks matter.
- `slp-builder-deepseek`: OpenCode mode `slp-builder` (permission rules + `*git*push*` deny; env push-block does not reach
  the shared OpenCode server). If `create_agent` errors (quota/mode), fall back to `slp-builder-sonnet`; no retry loop.
- Peers have no Paseo tools. They cannot ask you anything mid-run; questions arrive in `result.json` / the reply.
- Claude checkers/reviewers write only their own review JSON under `.slp/` (bash read-only). The advisor
  (`slp-advisor-sonnet`) writes nothing (not even under `.slp/`) and answers in chat; read its reply with
  `get_agent_activity`.
- `slp-checker-deepseek` (OpenCode agent `slp-checker`, edit denied, bash allowlist: read-only git/cat/ls/rg/grep/head/tail/wc)
  is the cross-family checker for Sonnet builders. It cannot write the review file: read its final JSON with
  `get_agent_activity` and save it yourself (prompts.md "Saving a checker reply"), then run `slp-evidence`.
  On quota/mode error fall back to `slp-checker-opus`; no retry loop.
