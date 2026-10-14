# Prompt templates for Peers (copy, fill the angle brackets)

Peers get their role prompt from the harness; these prompts carry only the task. Use ABSOLUTE paths.

## Builder, round 1
```
Slice <slice-id>. Worktree: <worktree>. Read <worktree>/.slp/slices/<slice-id>/brief.md and implement it.
Change only the paths in `files`. Make tests_must_pass pass. Stage ONLY the files you changed
(`git add <paths>`, never `git add -A`, never `.slp/`), commit on the current branch, never push.
Write <worktree>/.slp/slices/<slice-id>/result.json (valid JSON, shape = <SLP_ROOT>/templates/result.json) even when blocked.
Tenant isolation: never drop or weaken a merchant_id filter. Hard gate or ambiguity: stop, status blocked or
needs_input, explain in result.json; do not guess.
```

## Builder, fix round N (same agent: send_agent_prompt; new agent when the tier changes)
```
Round <N> for slice <slice-id>. Fix exactly the items below, nothing else, then commit and rewrite result.json.
<verify checks with status fail, copied from verify.<N-1>.json (name + evidence lines)>
<OR findings that survived slp-evidence, from review.<N-1>.checked.json: file:line, evidence, fix_hint>
```
For a new Sonnet builder (round 4+) prepend the original round-1 prompt and add: "Earlier rounds failed; here is
what failed: ...".

## Checker, Claude (slp-checker-sonnet / -opus): writes its own file
```
Review slice <slice-id>, round <N>, in worktree <worktree>. Inputs: <worktree>/.slp/slices/<slice-id>/brief.md,
result.json, verify.<N>.json, diff `git diff <base_ref>...HEAD` (+ `git diff` for uncommitted).
Write your review JSON (schema in your role prompt) to <worktree>/.slp/slices/<slice-id>/review.<N>.json. Evidence =
exact line text. Builder family: <gemini|deepseek|sonnet>. Risk notes: <tenant/auth/etc, or "none">.
```

## Checker, Codex (slp-checker-codex, for Sonnet builders): replies with JSON
```
Review slice <slice-id>, round <N>, in worktree <worktree>. Inputs: <worktree>/.slp/slices/<slice-id>/brief.md,
result.json, verify.<N>.json, diff `git diff <base_ref>...HEAD` (+ `git diff` for uncommitted).
Your FINAL message is ONE compact review JSON object (schema in your role prompt), nothing else: no prose, no code
fence. You cannot write files; the Lead saves your message. Evidence = exact line text.
Builder family: sonnet. Risk notes: <tenant/auth/etc, or "none">.
```

## Re-ask checker after evidence rejected everything
```
Your verdict was <fix|block> but slp-evidence rejected all findings: <reason per finding>. Re-check. Either return
findings that quote the exact line (file, line, evidence) or return verdict pass with notes.
```

## Final Reviewer (writes its own file)
```
Review the whole workstream in worktree <worktree>. Read <worktree>/.slp/workstream.md, every
.slp/slices/*/{brief.md,result.json,review.*.json,verify.*.json}, and the diff `git diff <base_branch>...HEAD`.
Also re-judge these deferred minor findings: <list or none>. Write the review JSON (slice_id "workstream") to
<worktree>/.slp/final-review.json.
```

## Advisor (design second opinion)
```
Question: <one decision>. Options considered: <A, B>. Read: <absolute paths>. Return recommendation, risks, unknowns,
evidence. Do not implement or edit anything.
```

## Saving a Codex checker reply
Codex peers are sandbox read-only and cannot write files. Fetch the reply with `get_agent_activity(agentId, limit=5)`,
copy the JSON object verbatim (no edits, no reformatting) into `<worktree>/.slp/slices/<slice-id>/review.<N>.json`
with Write, then run `slp-evidence`. If the activity output is truncated or not valid JSON, `send_agent_prompt`
"Resend the review JSON only, complete, compact, no prose" and fetch again (once). The same applies to a Claude
checker whose Write was refused (plan mode).
