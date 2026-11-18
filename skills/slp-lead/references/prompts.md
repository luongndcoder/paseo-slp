# Prompt templates for Peers (copy, fill the angle brackets)

Peers get their role prompt from the harness; these prompts carry only the task. Use ABSOLUTE paths.

## Builder, round 1
```
Slice <slice-id>. Worktree: <worktree>. Read <worktree>/.slp/slices/<slice-id>/brief.md and implement it.
Change only the paths in `files`. Make tests_must_pass pass. Stage ONLY the files you changed
(`git add <paths>`, never `git add -A`, never `.slp/`), commit on the current branch, never push.
Write <worktree>/.slp/slices/<slice-id>/result.json (valid JSON, shape = <SLP_ROOT>/templates/result.json) even when blocked.
Tenant isolation: never drop or weaken a merchant_id filter. Hard gate or ambiguity: stop, status blocked or
needs_input, explain in result.json; do not guess. The brief's design_choice is a proposal: if evidence shows it is
wrong, add scope_challenge {premise, evidence, proposal} to result.json instead of silently deviating.
```

## Builder, fix round N (same agent: send_agent_prompt; new agent when the tier changes)
```
Round <N> for slice <slice-id>. Fix exactly the items below, nothing else, then commit and rewrite result.json.
<if a scope_challenge was judged: "Your scope_challenge was accepted/rejected: <reason>; brief updated: <what>">
<verify checks with status fail, copied from verify.<N-1>.json (name + evidence lines)>
<OR findings that survived slp-evidence, from review.<N-1>.checked.json: file:line, evidence, fix_hint>
```
For a new Sonnet builder (round 4+) prepend the original round-1 prompt and add: "Earlier rounds failed; here is
what failed: ...".

## Checker (slp-checker-sonnet / -opus): writes its own file
```
Review slice <slice-id>, round <N>, in worktree <worktree>. Inputs: <worktree>/.slp/slices/<slice-id>/brief.md,
result.json, verify.<N>.json, diff `git diff <base_ref>...HEAD` (+ `git diff` for uncommitted).
Verified commit: head_sha <full SHA from verify.<N>.json>; run `git rev-parse HEAD` first and review only that commit.
Write your review JSON (schema in your role prompt, incl. head_sha and base_ref) to
<worktree>/.slp/slices/<slice-id>/review.<N>.json. Evidence = exact line text. No finding is a valid result.
Builder family: <gemini|deepseek|sonnet>. Risk notes: <tenant/auth/etc, or "none">.
```

## Checker (slp-checker-deepseek, for Sonnet builders): cannot write, replies with the JSON
```
Review slice <slice-id>, round <N>, in worktree <worktree>. Inputs: <worktree>/.slp/slices/<slice-id>/brief.md,
result.json, verify.<N>.json, diff `git diff <base_ref>...HEAD` (+ `git diff` for uncommitted).
Verified commit: head_sha <full SHA from verify.<N>.json>; run `git rev-parse HEAD` first and review only that commit.
You cannot write files. Your FINAL message must be the review JSON only (one compact line, schema in your role
prompt, incl. head_sha and base_ref), no prose, no code fence. Evidence = exact line text. No finding is a valid result.
Builder family: sonnet. Risk notes: <tenant/auth/etc, or "none">.
```
Then: `get_agent_activity`, save the JSON to `<worktree>/.slp/slices/<slice-id>/review.<N>.json`, run `slp-evidence`.

## Re-ask checker after evidence rejected everything
```
Your verdict was <fix|block> but slp-evidence rejected all findings: <reason per finding>. Re-check. Either return
findings that quote the exact line (file, line, evidence) or return verdict pass with notes. Keep head_sha =
`git rev-parse HEAD` (current HEAD: <sha>).
```

## Final Reviewer (writes its own file)
```
Review the whole workstream in worktree <worktree> at HEAD <full SHA> (run `git rev-parse HEAD` first). Read <worktree>/.slp/workstream.md, every
.slp/slices/*/{brief.md,result.json,review.*.json,verify.*.json}, and the diff `git diff <base_branch>...HEAD`.
Also re-judge these deferred minor findings: <list or none>. Write the review JSON (slice_id "workstream") to
<worktree>/.slp/final-review.json.
```

## Advisor (slp-advisor-sonnet, independent second opinion; plain-text reply, <= 40 lines)
Give the goal, constraints and the open decision. Do NOT hand over a pre-solved answer to confirm or reject, and do
not ask it to "find holes"; "no objection, proceed" is an acceptable reply.
```
Goal: <outcome and why>. Constraints: <binding limits>. Decision needed: <one decision>. Read: <absolute paths>.
(Optional, labelled hypothesis: <approach considered>.) Return recommendation, risks, unknowns, evidence.
Do not implement or edit anything.
```

## Saving a checker reply
Always for `slp-checker-deepseek` (it cannot write); for a Claude checker only when its Write was refused (e.g. plan mode). Fetch the reply with
`get_agent_activity(agentId, limit=5)`, copy the JSON object verbatim (no edits, no reformatting) into
`<worktree>/.slp/slices/<slice-id>/review.<N>.json` with Write, then run `slp-evidence`. If the activity output is
truncated or not valid JSON, `send_agent_prompt` "Resend the review JSON only, complete, compact, no prose" and fetch
again (once).
