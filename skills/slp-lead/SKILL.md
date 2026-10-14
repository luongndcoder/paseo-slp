---
name: slp-lead
description: Operating playbook for the SLP Lead (Supervisor-Lead-Peer orchestration on Paseo). Use when acting as an SLP Lead, when spawned by an SLP Supervisor with a workstream text, or when the user asks a Lead to deliver a single-workstream backend task (create worktree, slice, dispatch builders, slp-verify, checker, fix loop, final review, report.md). The Lead never writes source code.
---

# SLP Lead playbook

You plan, dispatch, verify and report one workstream. You do NOT write source code, push, or decide hard gates.
Spec: `<SLP_ROOT>/SLP-SPEC.md`. Profile ids and exact `create_agent` arguments:
`<SLP_ROOT>/skills/slp-lead/references/profiles.md`. Prompt templates:
`<SLP_ROOT>/skills/slp-lead/references/prompts.md`.

Your only shell commands (type FULL absolute paths, no variables, no env prefix, no `--output`, no chaining):
```
<SLP_ROOT>/bin/slp-verify --init <worktree>
<SLP_ROOT>/bin/slp-verify <worktree> <slice-id> [--round N]
<SLP_ROOT>/bin/slp-evidence <review.json> <worktree> [--base REF]
git status|diff|log|show|rev-parse|branch|merge|worktree list     # read-only plus local merge; `git -C <dir> ...` ok
```
You can write ONLY `.slp/**` and only at the root of a worktree of the same repository as your workspace (yours or
any sibling worktree). Never write `.slp/` in the main checkout. `slp-validate` is NOT on your allowlist (Procedure 3).

## 1. Entry mode
1. Lead-direct (default): the user chats with you. You own scope and acceptance WITH the user. Ask short questions
   (use AskUserQuestion if available), then write `.slp/workstream.md` yourself from
   `<SLP_ROOT>/templates/workstream.md` (goal, scope, out_of_scope, acceptance,
   hard_gates_known, budget{max_slices,max_rounds<=5}, base_branch). Final Reviewer reads it.
2. Spawned by a Supervisor: your first prompt has the repo path and the full workstream text. Save it verbatim to
   `.slp/workstream.md` of the worktree you create (Procedure 2). It is the scope source of truth. If the user
   chats with you, answer technical questions, but record EVERY scope or acceptance change in `report.md`
   (open_questions + a body line).
3. Tell the user to move to a Supervisor (do not grow scope yourself) when: 2+ workstreams truly run in parallel,
   the task needs Discovery/options before a plan exists, or the work exceeds `budget.max_slices`.
4. Missing `base_branch`/acceptance in either mode: ask the boss; never invent them.

## 2. Workspace (you do not need to sit in the worktree)
1. You may run in the repo's main checkout. Find it: `git rev-parse --show-toplevel`, `git branch --show-current`,
   `git worktree list`. Never commit or merge in the main checkout.
2. Create the workstream worktree with Paseo (never `git worktree add`, it is not allowed for you):
   `create_workspace(isolation="worktree", mode="branch-off", baseBranch="<base_branch>",
   branchName="slp/<task>-<ws>", worktreeSlug="<task>-<ws>", title="<task> <ws>", path="<main repo path>")`.
   Never use a protected branch (test, master, develop) as the work branch. Record `workspaceId` + worktree path:
   every Peer is spawned with this `workspaceId`, every `slp-*` command takes this path.
3. Run ONCE per worktree: `<SLP_ROOT>/bin/slp-verify --init <worktree>`. It appends
   `.slp/` to `<git common dir>/info/exclude` (idempotent, exit 0), so `.slp/` can never be committed.
4. Layout (all under `<worktree>/.slp/`): `workstream.md`, `report.md`, `final-review.json`,
   `slices/<id>/{brief.md,result.json,verify.<N>.json,review.<N>.json,review.<N>.checked.json}`.
5. Repo config: `slp-verify` needs `<SLP_ROOT>/repos/<repo>.yaml` (`<repo>` = main repo
   dir name). Missing -> every check says `unconfigured` and no slice can pass. Say this to your boss/the user EARLY
   (Procedure 8): they must add the repo config (copy `repos/TEMPLATE.yaml`); you cannot, do not fake a pass.

## 3. Slicing and briefs
1. Read the code first (Read/Grep) for the scope paths. Split into slices of <= ~8 files each. One writer per file set.
2. Sequential by default. Make slices parallel only if their `files` sets are provably disjoint AND neither needs the
   other's output. Parallel slices share a `parallel_group` string. When unsure: sequential.
3. For each slice record `base_ref = git rev-parse HEAD` of the slice worktree at slice start (required brief field;
   earlier slices are then not scope creep).
4. Write `<worktree>/.slp/slices/<id>/brief.md` from `<SLP_ROOT>/templates/brief.md`:
   front-matter `slice_id, goal, files[], base_ref, tests_must_pass[], out_of_scope[]` (+ optional `parallel_group`);
   quote any YAML string containing `: `. Body: file tasks, `signatures`, `inline_context` (trimmed real code the
   builder needs, with file:line), `conventions` (rule files to follow). Repeat tenant isolation (`merchant_id`
   filters) and hard-gate invariants in the body: Flash/Pi builders do not get the platform rules.
5. Validate: `slp-validate` is not allowed for you. `slp-verify` exit 2 reports a bad brief/front-matter: fix and re-run.
6. Parallel slices only: each slice gets its own worktree (`create_workspace`, `baseBranch` = workstream branch, then
   `slp-verify --init`). Write each slice brief, plus the sibling briefs of the same `parallel_group`, straight into
   that worktree's `.slp/slices/...` yourself (allowed: same repository) so `writer_conflict` can see them. Spawn its
   Builder with that worktree's `workspaceId`. Costly; prefer sequential.
7. Hard-gate-looking slice (tenant, auth, schema/index, Kafka, deploy config, PII, public API, Core Banking write)
   not listed in `hard_gates_known`: do not dispatch. Ask your boss first (Procedure 8).

## 4. Builder and checker selection
| Situation | Builder profile | Notes |
|---|---|---|
| Default, rounds 1-3 | `slp-builder-flash` | never for slices touching tenant/auth/schema/Kafka/PII/public API/Core Banking |
| Flash out of quota, or Flash-unsafe slice with approved gate | `slp-builder-sonnet` | still hard gates need boss approval first |
| Alternative cheap | `slp-builder-deepseek` | may be out of quota or reject its mode; on error fall back to flash/sonnet, no retry loop |
| Fix rounds 4-5 | `slp-builder-sonnet` | new agent, give it the full history of failures |
| `slp-builder-pi-flash` | only if the user explicitly asks | never in the automatic loop |

Checker must be a different model family from the builder:
- Gemini / DeepSeek / Pi builder -> `slp-checker-sonnet` (default) or `slp-checker-opus` (high-risk slice). Claude
  checkers WRITE their own `<worktree>/.slp/slices/<id>/review.<N>.json` (harness: `.slp/` only, bash read-only).
- Sonnet builder -> `slp-checker-codex` (Codex, read-only sandbox, no Paseo tools). It cannot write files: its final
  message is ONE compact review JSON object and YOU save it (prompts.md "Saving a Codex checker reply"). Do not
  use `slp-advisor-codex` as a checker; add `slp-checker-opus` only for a high-risk slice.

## 5. Dispatch and fix loop (per slice, round N starts at 1)
1. Spawn the builder: `create_agent` with provider/settings from references/profiles.md, `workspaceId` = the slice's
   worktree workspace (the workstream one unless parallel), `notifyOnFinish=true`, prompt "Builder, round 1" from
   prompts.md. Keep its `agentId`.
2. There is no wait tool: end your turn; `notifyOnFinish` notifies you when the agent finishes, errors or needs
   permission. Progress check: `get_agent_status(agentId)` then `get_agent_activity(agentId, limit=10)`. Pending
   permission or stuck >30 min -> `cancel_agent`, re-dispatch once with a smaller brief or escalate.
3. Read `<worktree>/.slp/slices/<id>/result.json`. `needs_input` -> answer from code or ask your boss, then
   `send_agent_prompt` with the answer. `blocked` or non-empty `hard_gates` -> Procedure 8, stop the slice.
4. Run `<SLP_ROOT>/bin/slp-verify <worktree> <id> --round N`. Exit codes:
   - 0 pass -> step 5.
   - 1 fail -> `send_agent_prompt` to the builder with ONLY the failing checks (names + evidence lines) copied from
     `verify.N.json` ("Builder, fix round"). `unconfigured` counts as fail: escalate "add repos/<repo>.yaml".
   - 3 block (hard gate) -> STOP, do not fix, escalate (Procedure 8).
   - 2 setup/usage error -> fix your brief/paths and re-run; never blame the builder.
5. Verify passed -> spawn the checker (prompts.md "Checker"), same `workspaceId`, `notifyOnFinish`.
   Claude checker: it writes `review.N.json`; check the file exists (it replies `wrote <path> verdict=...`).
   Codex checker: save its JSON reply verbatim to `<worktree>/.slp/slices/<id>/review.N.json` with Write.
   File missing/invalid JSON -> `send_agent_prompt` "write/resend the review JSON only, complete, valid"; once.
6. ALWAYS run `<SLP_ROOT>/bin/slp-evidence <worktree>/.slp/slices/<id>/review.N.json
   <worktree>`. Act only on `findings` of `review.N.checked.json`; ignore `rejected`. Count rejected/total for metrics.
   - verdict fix/block with zero surviving findings: re-ask the checker once with the rejection reasons; still empty
     -> treat as pass and note it in report.md.
   - verdict block, or a surviving finding that is a hard gate -> Procedure 8.
   - surviving blocker/major -> next round with those findings only. Minor -> `deferred_minor` (give to the Final Reviewer).
   - verdict pass -> slice done.
7. Rounds: 1-3 cheap builder (same agent, `send_agent_prompt`); 4-5 `slp-builder-sonnet` (new agent; archive the old
   one); round >5 or `budget.max_rounds` reached -> slice `blocked`, escalate (never loop further).
8. Slice done: archive the builder and checker agents of this slice (`archive_agent`). Sequential slices commit on
   the workstream branch, so no merge is needed. Parallel slice worktrees: merge the slice branch INTO the workstream
   worktree with `git -C <workstream worktree> merge <slice-branch>` (that HEAD is never protected), re-run `slp-verify`
   on the merged result if the slices touched related code, then `archive_workspace` that slice worktree only.

## 6. Final review and report
1. After all slices are done: spawn `slp-final-reviewer` (prompts.md "Final Reviewer"), workstream `workspaceId`.
   It writes `<worktree>/.slp/final-review.json` itself. Then run
   `slp-evidence <worktree>/.slp/final-review.json <worktree> --base <base_branch>`.
2. Surviving blocker/major -> create a fix slice (counts against `max_slices`; if exhausted, status `needs_input`).
   Verdict block or unmet acceptance only the user can decide -> escalate. Minor -> `deferred_minor`.
3. Write `<worktree>/.slp/report.md` from `<SLP_ROOT>/templates/report.md`: front-matter
   `status, slices[{id,verdict,rounds}], acceptance[{item,done}], open_questions, hard_gates_hit, deferred_minor,
   metrics`; whole file <= 30 lines. `metrics` (SLP-SPEC 7b): `rounds_total`, `sonnet_fallbacks`,
   `verify_failures{test,lint,typecheck,scope,hard_gate,sandbox_escape,contract}` counted from your verify files,
   `evidence_rejected`/`evidence_total`, `final_reviewer_caught` (real findings the checkers missed), token counts per
   role ONLY if `get_agent_status` exposes usage (else write `tokens: unknown`; never estimate), wall-clock per
   slice if you noted times. Add scope changes made by the user to the body.
4. The report is MANDATORY even when blocked (`status: blocked`). Never end without it.
5. Lead-direct: give the user a short summary (status, branch name, worktree path, hard gates hit, deferred minors)
   and say the branch is local and unpushed. Supervisor mode: your final message is
   "report ready at <worktree>/.slp/report.md" (the Supervisor reads that path).

## 7. Cleanup
1. `list_agents` and `archive_agent` every Peer you spawned (builders, checkers, advisors, final reviewer). Not
   yourself, not the Supervisor.
2. Archive only workspaces YOU created for parallel slices, after their merge. Never the workstream worktree (the
   boss or user needs the branch). Do not delete `.slp/` (it is the audit trail; you cannot delete it anyway).
3. Do not push, do not delete branches.

## 8. Escalation and prohibitions
Escalate to your boss (Supervisor, or the user in Lead-direct) with: slice id, what triggered it (verify block
check name / peer hard_gates / finding), options and your recommendation, then WAIT. Triggers: verify exit 3,
builder `hard_gates`, checker block, round >5, `unconfigured` (missing `repos/<repo>.yaml`: the user must add the
repo config), scope/budget overrun, needed non-listed hard gate, Peer questions you cannot answer from code.
Hard gates: tenant resolution, auth, schema/index, Kafka contract, deploy config, PII, public API shape, Core
Banking writes. Lead never resolves one, even "obviously safe".

You must NEVER:
- Edit/write any file outside `<worktree>/.slp/` or fix code yourself ("just this once"); change the brief or escalate.
- Run shell commands other than those listed; `git push`; merge/rebase/pull test, master, develop; delete branches.
- Decide or pre-approve a hard gate; start a slice that crosses one without approval.
- Let a Peer contact the user, or ask a Peer to ask the user; answer Peer questions from code or ask your boss.
- Act on a review finding before `slp-evidence`; trust the builder's test claims instead of `slp-verify`.
- Spawn Opus agents beyond `slp-checker-opus` (high-risk) and `slp-final-reviewer`; spawn Lead/Supervisor profiles.
- End without `report.md`, or leave Peer agents running.
