# SLP profiles -> create_agent arguments

Source of truth is the live Paseo profile list (MCP `list_profiles`, snapshot 2026-10-14). There is no `profile`
parameter on `create_agent`: copy the fields below. If `list_profiles` disagrees with this file, the live list wins.

`create_agent` shape: `{title (<=60 chars), provider: "<provider>/<model>", initialPrompt, workspaceId?,
settings: {modeId?, thinkingOptionId?, features?}, notifyOnFinish: true}`.
Pass `workspaceId` = the workstream worktree's workspace (from `create_workspace`) for every Peer, so it runs in the
worktree and finds `.slp/` there; the Supervisor passes the repo's main workspace id for a Lead. Omit `settings` keys
the profile leaves empty. Never invent `modeId` for `slp-advisor-codex`, `slp-checker-codex` and
`slp-builder-pi-flash` (none on purpose: a Codex mode would override the read-only sandbox).

| Profile id | provider (argument) | modeId | thinkingOptionId | Use |
|---|---|---|---|---|
| slp-lead | `slp-lead/claude-sonnet-5-5` | bypassPermissions | medium | Supervisor spawns one per workstream |
| slp-builder-flash | `antigravity/gemini-4-argon-medium` | full-access | (none) | default builder, rounds 1-3 |
| slp-builder-deepseek | `slp-opencode-builder/opencode-go/deepseek-v4.1-flash` | slp-builder | default | alternative cheap builder; may be out of quota |
| slp-builder-sonnet | `slp-claude-builder/claude-sonnet-5-5` | bypassPermissions | medium | rounds 4-5 and fallback |
| slp-builder-pi-flash | `slp-pi-builder/openai-api/gemini-3.8-flash-high` | (none) | (none) | ONLY when the user asks |
| slp-checker-sonnet | `slp-claude-checker/claude-sonnet-5-5` | bypassPermissions* | high | default checker (non-Claude builders); writes review.N.json |
| slp-checker-opus | `slp-claude-checker/claude-opus-5-5` | bypassPermissions* | medium | high-risk slices; writes review.N.json |
| slp-checker-codex | `slp-codex-checker/gpt-6-astra` | (none) | medium | checker for SONNET builders; replies with review JSON, Lead saves it |
| slp-advisor-codex | `slp-codex-advisor/gpt-6-astra` | (none) | high | design opinion only (not a checker) |
| slp-final-reviewer | `slp-claude-final-reviewer/claude-opus-5-5` | bypassPermissions* | high | whole workstream, once; writes final-review.json |
| slp-integration-reviewer | `slp-claude-integration-reviewer/claude-opus-5-5` | bypassPermissions* | medium | Supervisor only; writes integration-review.json |

\* Claude checker/reviewer providers now allow writes ONLY under `.slp/` (hook) with read-only bash, so the mode is
`bypassPermissions` (SLP-SPEC P3 decision 8). If the live `list_profiles` still says `plan`, the Write tool is refused:
the agent then replies with the JSON and you save it yourself (same as the Codex checker).

Example (default builder):
```
create_agent(title="slp s1-export-service r1", provider="antigravity/gemini-4-argon-medium",
  settings={modeId:"full-access"}, notifyOnFinish=true,
  initialPrompt=<builder prompt from prompts.md>)
```
Example (Claude checker, in the workstream worktree):
```
create_agent(title="slp check s1 r1", provider="slp-claude-checker/claude-sonnet-5-5", workspaceId=<worktree workspace>,
  settings={modeId:"bypassPermissions", thinkingOptionId:"high"}, notifyOnFinish=true, initialPrompt=<checker prompt>)
```
Example (Codex checker for a Sonnet builder; no modeId):
```
create_agent(title="slp check s1 r1 codex", provider="slp-codex-checker/gpt-6-astra", workspaceId=<worktree workspace>,
  settings={thinkingOptionId:"medium"}, notifyOnFinish=true, initialPrompt=<checker prompt>)
```
Example (worktree for a workstream):
```
create_workspace(isolation="worktree", mode="branch-off", baseBranch="<base_branch>", branchName="slp/<task>-<ws>",
  worktreeSlug="<task>-<ws>", title="<task> <ws>", path="<main repo path>")   # then slp-verify --init <worktree>
```

## Known harness caveats (SLP-SPEC section 5, P1 log)
- `slp-builder-flash` runs WITHOUT an SLP harness (Paseo cannot extend antigravity) and does not receive Mobio
  platform rules: the brief must repeat tenant/hard-gate invariants, and `slp-verify` is the only safety net.
- `slp-builder-deepseek`: the OpenCode mode `slp-builder` was rejected by Paseo 0.11.2 and the profile was waiting for
  quota. If `create_agent` errors (mode/quota), switch to flash; do not retry in a loop.
- Peers have no Paseo tools. They cannot ask you anything mid-run; questions arrive in `result.json` / the reply.
- Claude checkers/reviewers write only their own review JSON under `.slp/` (bash read-only). Codex peers
  (`slp-checker-codex`, `slp-advisor-codex`) are sandbox read-only and cannot write: you save the JSON reply.
