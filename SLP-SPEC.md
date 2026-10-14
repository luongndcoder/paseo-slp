# SLP Spec — Supervisor · Lead · Peer trên Paseo

> Trạng thái: DRAFT chờ duyệt · 2026-10-14
> Quyết định đã chốt: Peer giao tiếp qua artifact + Lead route · 1 Lead / workstream · harness riêng cho mọi provider (multi-model) · verify bằng script deterministic · Final Reviewer ở Lead · skill mới `slp-*`, bỏ `paseo-be-conductor` sau khi harvest.

> **Môi trường:** Paseo daemon chạy từ **bản fork** `<WORKSPACE_DIR>/paseo` (0.11.2). Mọi wiring ở §5 được xác minh trên source fork này → sau mỗi lần cập nhật fork (pull/merge upstream) phải `npm run build` rồi chạy lại `slp-conformance`. CLI Homebrew 0.10.3 cũ hơn và từ chối config hiện tại — scripts ưu tiên CLI của fork.

## 1. Mục tiêu

- Tách 3 tầng: mỗi tầng có quyền, harness, prompt và contract riêng.
- Chặn "làm hộ" bằng **quyền thật** (tool deny, mode, paseoTools), không bằng lời dặn.
- Opus chỉ ở Supervisor: đọc report ngắn, viết brief ngắn.
- Mọi hand-off qua schema cố định để giảm ảo giác.

Ngoài phạm vi: thay đổi Paseo core, provider mới, logic nghiệp vụ Mobio.

## 2. Ba tầng

| | Supervisor | Lead | Peer |
|---|---|---|---|
| Số lượng | 1 / task | 1 / workstream | n / slice |
| Nói với user | Có | Có (entry trực tiếp hoặc user vào chat) | Không |
| Spawn agent | Chỉ Lead (+ Final Reviewer, Advisor) | Chỉ Peer | Không |
| Sửa code | Không | Không (chỉ ghi `.slp/`) | Builder: có, trong scope slice; Peer khác: không |
| Đọc diff | Không — chỉ đọc report | Có | Checker: có |
| Model | Opus · thinking medium | Sonnet · medium | Theo vai (mục 5) |

### Trách nhiệm

**Supervisor** — ông sếp, chỉ quan tâm kết quả: Discovery với user (grill/options/devil/spike) → chia workstream → chọn Lead profile → nghiệm thu `report.md` → escalate hard gate cho user. Không đọc diff. Nếu ≥ 2 workstream chia sẻ contract (API / schema / Kafka) → gọi **Integration Reviewer** (Peer, chỉ trả verdict).

**Lead**: Đọc code của workstream → chia slice (≤ ~8 file/slice) → viết slice brief **có inline code context** → dispatch Builder → chạy `slp-verify` → dispatch Checker (khác họ model) → fix loop → merge slice vào branch workstream (local, không push) → **Final Reviewer** cho cả workstream → report lên Supervisor.

**`slp-verify`** (script, không phải LLM): chạy test + lint + typecheck của repo trong worktree → `.slp/slices/<id>/verify.json`. Lead chỉ được phép chạy đúng lệnh này (permission allow 1 command). Kết quả là bằng chứng độc lập với lời khai của Builder. Verify fail không rõ nguyên nhân → Checker triage.

**Peer**: làm đúng 1 việc trong brief, trả result/review JSON. Gặp ambiguity hoặc hard gate → ghi vào `questions`/`hard_gates`, dừng, **không tự quyết**.

## 3. Luồng & escalation

```
User ⇄ Supervisor ──workstream brief──▶ Lead ──slice brief──▶ Builder ──result.json──▶ Lead
                                          │                                              │
                                          └──diff + brief──▶ Checker ──review.json──▶ Lead
Lead ──slp-verify──▶ verify.json            (deterministic, 0 token LLM)
Lead ──(mọi slice xong)──▶ Final Reviewer ──▶ Lead ──report.md──▶ Supervisor ──▶ User
Supervisor ──(≥2 workstream chung contract)──▶ Integration Reviewer ──verdict──▶ Supervisor
```

- **Hai entry point:**
  - **Lead-direct** (task đơn giản, 1 workstream): user chat thẳng với Lead. Lead đóng vai sếp của chính nó: hỏi user, nhận scope, nghiệm thu với user. Không có Supervisor.
  - **Supervisor** (task nhiều workstream / cần Discovery): Supervisor spawn Lead. User vẫn có thể vào chat với Lead; Lead trả lời kỹ thuật được, nhưng **thay đổi scope / acceptance phải ghi vào `report.md` để Supervisor biết** (Supervisor là nguồn sự thật của scope).
- Escalation: Peer → Lead → (Supervisor nếu có) → User. Peer không bao giờ hỏi user.
- **Fix loop** (Lead): round 1–3 Builder rẻ · round 4–5 Builder Sonnet · quá 5 → `blocked`, escalate.
- **Hard gate** (tenant, auth, schema/index, Kafka contract, deploy config, PII, public API, Core Banking write): Peer flag → Lead dừng slice → người đứng trên Lead (Supervisor, hoặc user ở chế độ Lead-direct) quyết. Không tầng nào tự implement khi chưa được duyệt.
- **Chọn entry**: **mặc định Lead-direct (2 tầng)**. Chỉ dùng Supervisor khi có ≥ 2 workstream song song thật hoặc cần Discovery. Lead tự nhận thấy task vượt 1 workstream → đề nghị user chuyển lên Supervisor, không tự phình scope.
- **1 writer / file set**: slice chạy song song phải có `files` rời nhau (Lead kiểm trước khi dispatch, `slp-verify` kiểm sau). Slice phụ thuộc nhau → **chạy tuần tự**. Mặc định tuần tự; chỉ song song khi chứng minh được độc lập. (Google 2026: multi-agent làm task tuần tự tệ đi 39–70%; Cursor 2026: nhiều writer chung file → nghẽn.)

## 4. Contract (artifact trong worktree)

Thư mục `.slp/` trong worktree, ignore qua `.git/info/exclude` (không đổi repo).

```
.slp/
  workstream.md            # Supervisor → Lead
  report.md                # Lead → Supervisor
  slices/<slice-id>/
    brief.md               # Lead → Builder
    result.json            # Builder → Lead
    verify.<round>.json    # slp-verify → Lead
    review.<round>.json    # Checker → Lead
  final-review.json        # Final Reviewer → Lead (schema như review.json, phạm vi cả workstream)
```

**workstream.md**: `goal` · `scope` (modules/paths) · `out_of_scope` · `acceptance` (checklist kiểm được) · `hard_gates_known` · `budget` (max slices, max rounds) · `base_branch`.

**brief.md**: `slice_id` · `goal` · `files` (path + việc cần làm) · `signatures` · `inline_context` (code liên quan, đã cắt) · `tests_must_pass` · `out_of_scope` · `conventions` (link rule cần tuân).

**result.json**
```json
{"slice_id":"", "status":"done|blocked|needs_input", "files_changed":[], "tests":{"cmd":"","passed":0,"failed":0},
 "deviations":[], "questions":[], "hard_gates":[], "summary":"≤5 dòng"}
```

**review.json**
```json
{"slice_id":"", "round":1, "verdict":"pass|fix|block",
 "findings":[{"lens":"business|security|perf|scale","severity":"blocker|major|minor","file":"","line":0,"evidence":"","fix_hint":""}]}
```
Finding không có `evidence` hoặc evidence không khớp diff → bị `slp-evidence` loại tự động (mục 4b).

**report.md**: `status` · slices (id, verdict, rounds) · `acceptance` tick · `open_questions` · `hard_gates_hit` · `deferred_minor` · `metrics` (mục 7b) · ≤ 30 dòng. **Bắt buộc** — Lead/Peer không được kết thúc mà thiếu artifact trả về (Cognition 2026: child không tự report là failure mode chính).

## 4b. Verification harness

Nguyên tắc: **cái gì deterministic được thì không dùng LLM**; LLM review chỉ chạy sau khi lớp deterministic pass. Tất cả nằm trong `<WORKSPACE_DIR>/paseo-slp/`.

```
paseo-slp/
  bin/slp-verify        # lớp 1 — per slice
  bin/slp-evidence      # lớp 2 — per review
  bin/slp-conformance   # lớp 3 — per harness change
  schemas/*.json        # JSON Schema cho 5 artifact
  repos/<repo>.yaml     # lệnh test/lint/typecheck + hard-gate patterns riêng từng repo (không sửa repo)
```

**Lớp 1 — `slp-verify <worktree> <slice-id>` → `verify.<round>.json`** (0 token LLM)
| Check | Fail khi |
|---|---|
| test / lint / typecheck | lệnh trong `repos/<repo>.yaml` exit ≠ 0 |
| scope | file thay đổi ∉ `brief.files` |
| writer conflict | file thay đổi trùng slice song song khác |
| hard-gate scan | diff khớp pattern: filter `merchant_id` / tenant resolve, migration / index, Kafka schema, auth/permission, deploy config (ArgoCD/K8s/Jenkins), PII field → **block + escalate**, không phải fix |
| sandbox-escape guard | diff đụng `.claude/`, `.codex/`, `.opencode/`, `.git/hooks`, `.gitconfig`, `package.json` scripts/hooks, `.mcp.json`, Makefile target chạy tự động (Pillar 2026: 7 vụ thoát sandbox qua config/hook) |
| contract | `result.json` sai schema |

**Lớp 2 — `slp-evidence <review.json> <diff>`**: mỗi finding phải có `file:line` nằm trong diff (hoặc file được brief tham chiếu) và `evidence` khớp nội dung dòng đó → finding bịa bị loại trước khi tới Lead.

**Lớp 3 — `slp-conformance`** (chạy sau mỗi `slp-build`, update Paseo, update mobio-kit, đổi profile): spawn từng role bằng model rẻ nhất cùng provider/mode, probe:
- role read-only thử ghi file tạm → phải bị chặn (như test P0);
- Peer thử gọi Paseo tool / Agent tool → phải không có;
- Lead thử Bash ngoài `slp-verify` → phải bị chặn;
- skill/MCP thấy được ⊆ whitelist (`/context` hoặc tool list);
- Codex peer thử ghi → bị sandbox chặn.
Kết quả → `conformance-<date>.json`; bất kỳ probe fail → không dùng SLP tới khi sửa. (Chưa thấy ai công bố bộ test tương tự — research 2026-10-14.)

## 5. Mapping Paseo

### Role = custom provider (harness)

| Provider id | extends | Harness | paseoTools |
|---|---|---|---|
| `slp-supervisor` | claude | wrapper: `--settings supervisor.json --append-system-prompt <role>` | bật; tắt tool ngoài điều phối (workspace/terminal/browser/schedule) |
| `slp-lead` | claude | wrapper: `--settings lead.json …` | create_agent/send/status/activity/archive/cancel agent, create_workspace, list_* (không có tool wait → notifyOnFinish) |
| `slp-peer-claude` | claude | wrapper: `--settings peer-<role>.json …` | tắt |
| `slp-peer-opencode` | opencode | `--agent slp-<role>` (agent file: prompt, tools, permission) | tắt |
| `slp-peer-codex` | codex | `-p slp-<role>` (profile trong config.toml: instructions, sandbox, reasoning) | tắt |
| `slp-peer-pi` | pi | `--append-system-prompt <role.md>` · `--tools`/`--exclude-tools` · `--no-mcp` · `-e` | tắt |
| `slp-peer-agy` | antigravity | `--agent slp-<role>` · `--mode` · `--sandbox` | tắt |

`<role>.json` (Claude) chứa: `skillOverrides` (whitelist theo tầng — mục 6), `permissions.deny`, `hooks`, `env`.
**Một nguồn role duy nhất**: `roles/<role>/role.md` (prompt) + `roles/<role>/policy.yaml` (tools/skills/mode) → script `slp-build` sinh config cho 5 họ provider. Sửa role 1 chỗ.
Không set `CLAUDE_CONFIG_DIR` / `CODEX_HOME` → zerosub vẫn xoay account.

### Wiring đã kiểm tra trong source Paseo (2026-10-14)
`command` trong provider override = **thay toàn bộ argv** (không có mode append trong config.json); Paseo/SDK vẫn nối cờ của nó **sau** argv của mình → với cờ 1 giá trị, cờ của Paseo thắng.

| Provider | Cách Paseo spawn | Gắn role thế nào | Không được dùng |
|---|---|---|---|
| Claude | Agent SDK, stream-json; SDK thêm `--model --mcp-config(paseo) --setting-sources=user,project,local --permission-mode --allowed/disallowedTools` | `command: ["claude","--settings",<role.json>,"--strict-mcp-config","--mcp-config",<role-mcp.json>]`; role prompt: thử `--append-system-prompt`, fallback hook `SessionStart` trong role settings in `role.md` | — (Paseo chỉ tự truyền `--settings` khi fast mode/ultracode) |
| Codex | `codex <argv> app-server` | `command: ["codex","-c","sandbox_mode=\"read-only\"","-c","developer_instructions=..."]` (không phụ thuộc file `config.toml` theo account); tắt multi-agent: `-c features.multi_agent=false -c features.multi_agent_v2=false -c model_catalog_json=<build/codex/catalog.json>` (catalog = `codex debug models` bỏ `multi_agent_version`, vì model v2 tự bật `collaboration.spawn_agent` bất kể feature flag); **không set `modeId`** cho role read-only (preset của mode ghi đè sandbox) | `--sandbox` (vô nghĩa với app-server) |
| OpenCode | `opencode <argv> serve` | định nghĩa agent `slp-builder` qua `env.OPENCODE_CONFIG_CONTENT` (JSON: prompt, permission edit/bash glob, task deny, steps); profile `modeId: "slp-builder"` (mode = agent name) | `--agent` |
| Pi | `pi <argv> --mode rpc --mcp-config <paseo> …` | `command: ["pi","--append-system-prompt",<role.md>,"--tools",…]` | `--no-mcp` (giết adapter Paseo) |
| Antigravity | plugin; Paseo luôn thêm `--dangerously-skip-permissions` | `command: ["agy","--agent",…]` (prefix); không có Paseo tools | `--mode plan` / `--sandbox` chưa chắc tương thích → test |

Hệ quả quan trọng:
- **`daemon.appendSystemPrompt` (Mobio rules) chỉ tới Claude và Codex**, KHÔNG tới Antigravity / Pi / ACP → builder Flash/Pi hiện **không nhận rule tenant isolation / hard gate**. Role prompt của các builder này phải tự chứa invariants; `slp-verify` hard-gate scan là lưới an toàn bắt buộc.
- `create_agent` (MCP) không có field `systemPrompt` → role prompt chỉ có thể đi qua provider (command/env/settings).
- Env: `provider.env` < env do plugin (zerosub) inject → không bao giờ set `CLAUDE_CONFIG_DIR`/`CODEX_HOME` trong role provider.
- `paseoTools.disabledTools` dùng tên trần (`create_workspace`, `create_terminal`, `create_schedule`, `browser_*`…); `enabled:false` = không inject Paseo MCP (trừ `speak`).

Unknown → thành probe trong `slp-conformance` (P1): Claude có gộp `--append-system-prompt` với system prompt append của SDK không; `--settings` của mình có còn hiệu lực dưới `--setting-sources` của SDK không; Codex app-server có tôn trọng `-c sandbox_mode` khi không set modeId không; `agy` có chấp nhận `--agent` cạnh `--dangerously-skip-permissions` không.

### Antigravity (agy) — cơ chế đã xác minh (2026-10-14)
- `agy --agent <name>` nạp agent Markdown: global `~/.gemini/config/agents/<name>.md` (hoặc project `.agents/agents/`). Body = system prompt; frontmatter `tools:` = allowlist **vẫn hiệu lực dưới `--dangerously-skip-permissions`** (probe: agent chỉ có view/list không ghi được file).
- Không có cờ/env system prompt; `GEMINI.md`/`AGENTS.md`/`.agents/rules` luôn nạp → không dùng cho khác biệt theo role.
- Paseo: provider `command[1..]` = prefix, `env` được truyền → wiring: `command: ["agy","--agent","slp-<role>"]` + `slp-build --apply` sinh `~/.gemini/config/agents/slp-<role>.md` (role.md + Mobio invariants).
- **Provider (2026-10-14, đã kiểm tra)**: `extends: "antigravity"` bị từ chối (fatal lúc khởi động) => không có provider `slp-agy-builder`. Quyết định: `slp-build` OVERRIDE provider plugin built-in `agents.providers.antigravity = {command:["agy","--agent","slp-agy-builder"], paseoTools:{enabled:false}}` (không `extends`; daemon 0.11.2 chấp nhận, argv agent = `agy --agent slp-agy-builder` + cờ Paseo). Hệ quả đã chấp nhận: MỌI agent Antigravity chạy qua Paseo (gồm profile `slp-builder-flash`) là SLP builder. Đây là key non-slp duy nhất slp-build ghi. Quan sát: `daemon.appendSystemPrompt` được Paseo ghép vào đầu prompt của agy (preview hội thoại).
- Allowlist `tools:` dùng tên registry (`view_file, list_dir, grep_search, find_by_name, write_to_file, replace_file_content, multi_replace_file_content, run_command`); `init.tools` luôn là catalog đầy đủ (không phản ánh allowlist); tên không có trong registry (vd `command_status`) => agent không khởi động. `manage_task` luôn hiện. Skills toàn cục (be-*, ...) vẫn thấy được.
- Enforce được: prompt + allowlist tool. KHÔNG enforce được: giới hạn đường dẫn ghi (có write tool là ghi mọi nơi) → `slp-verify` scope/sandbox-escape là lưới bắt buộc. `run_command` = lối thoát, bỏ khỏi role read-only. Tên tool có thể drift → mỗi lần đổi allowlist chạy lại conformance. File agent parse lỗi bị bỏ qua im lặng.

### Enforcement chi tiết (bẫy đã biết)
| Bẫy | Hệ quả | Cách chặn trong SLP |
|---|---|---|
| Claude: `disallowedTools` có specifier (`Bash(git push *)`) xoá **cả tool** | Lead mất Bash hoàn toàn hoặc không chặn được lệnh cụ thể | Lead: `permissions.allow: ["Bash(<path>/slp-verify *)"]` + `permissions.deny` Bash còn lại + hook `PreToolUse` từ chối mọi Bash khác |
| Claude: subagent bỏ qua `permissionMode` riêng nếu cha chạy `bypassPermissions` | Peer spawn subagent nội bộ → kế thừa bypass | Peer: deny tool `Agent`/`Task`; Lead chỉ dispatch qua Paseo (tiến trình riêng, mode riêng) |
| Codex: instructions không tạo sandbox; per-agent không override được sandbox | "Read-only" chỉ trên giấy | `slp-peer-codex` khởi chạy với `--sandbox read-only` cho advisor/checker; builder Codex dùng `workspace-write` |
| OpenCode: quyền theo agent (`edit`, `bash` glob, `permission.task`, `steps`) | — (đây là điểm mạnh) | Builder OpenCode: `bash` chỉ allow lệnh test/lint, `task` deny, `steps` cap |
| Agent ghi hook/config rồi tool khác chạy ngoài sandbox | Thoát sandbox | sandbox-escape guard trong `slp-verify` (mục 4b) |
| Hook Claude `TaskCompleted`/`TeammateIdle` exit 2 chặn kết thúc | — | Dùng để ép Peer/Lead không kết thúc khi thiếu artifact |

### Profile (model) — đang chạy từ 2026-10-14 (11 profile `slp-*`)

| Profile | Provider **hiện tại** → **đích P1** | Model | Mode | Thinking |
|---|---|---|---|---|
| `slp-supervisor` | claude → slp-supervisor | claude-opus-5-5 | bypassPermissions* | medium |
| `slp-lead` | claude → slp-lead | claude-sonnet-5-5 | bypassPermissions* | medium |
| `slp-builder-flash` | antigravity → slp-peer-agy | gemini-4-argon-medium | full-access | — |
| `slp-builder-deepseek` | be-opencode-worker → slp-peer-opencode | opencode-go/deepseek-v4.1-flash | — | default |
| `slp-builder-sonnet` | be-claude-worker → slp-peer-claude | claude-sonnet-5-5 | bypassPermissions | medium |
| `slp-builder-pi-flash` | pi → slp-peer-pi | openai-api/gemini-3.8-flash-high | — | — |
| `slp-checker-sonnet` | be-claude-worker → slp-peer-claude | claude-sonnet-5-5 | plan | high |
| `slp-checker-opus` | be-claude-worker → slp-peer-claude | claude-opus-5-5 | plan | medium |
| `slp-advisor-codex` | be-codex-worker → slp-peer-codex | gpt-6-astra | auto → read-only sandbox | high |
| `slp-final-reviewer` | be-claude-worker → slp-peer-claude | claude-opus-5-5 | plan | high |
| `slp-integration-reviewer` | be-claude-worker → slp-peer-claude | claude-opus-5-5 | plan | medium |

\* từ P1: quyền ghi bị chặn bằng `permissions.deny` + hook trong settings của role (Supervisor: mọi Edit/Write; Lead: Edit/Write ngoài `.slp/**`, Bash ngoài `slp-verify`). Trước P1 mới chỉ chặn bằng prompt.
Quy tắc tên: `SLP <Tầng> · <Vai> · <Model thật>` — tên phải khớp provider/model.
Icon = vai, màu = nhóm: Supervisor `compass`/violet · Lead `layers`/sky · Builder `hammer`/emerald · Checker `shield`/orange · Final Reviewer `eye`/amber · Integration Reviewer `gitBranch`/amber · Advisor `brain`/pink. (Khoá icon/màu hợp lệ: `app/src/agent-profiles/internal/profile-appearance.ts`, `styles/identity-colors.ts` trong fork.)

## 6. Skill whitelist theo tầng

| Tầng | Skills auto | Ghi chú |
|---|---|---|
| Supervisor | paseo, be-research, (grill / devil / sa-* qua `/`) | không skill implement |
| Lead | paseo, be-git, be-troubleshoot | be-plan slice logic nằm trong role prompt |
| Builder (Claude) | be-git, be-api-docs-sync | không skill brainstorm/plan |
| Checker | (lens nằm trong role prompt) | không skill implement |
| Lark/jenkins | chỉ Supervisor | Peer không cần |

Mọi skill khác → `user-invocable-only` trong settings của tầng.

## 7. Rollout

| Phase | Việc | Kiểm chứng |
|---|---|---|
| P0 | Fix 3 profile lệch tên; checker → `mode plan` + tắt paseoTools | `list_profiles` khớp provider/model; checker thử Edit bị chặn |
| P1 | `roles/` (role.md + policy.yaml) + `slp-build` → config cho Claude, OpenCode, Codex, Pi, Antigravity; khai báo provider trong Paseo | mỗi role/provider: chỉ thấy tool + skill trong whitelist; role read-only thử ghi bị chặn |
| P2 | Role prompt (EN, ≤ 60 dòng/role) + `schemas/` + `slp-verify` + `slp-evidence` + `slp-conformance` + `repos/<repo>.yaml` cho 1 repo pilot | dry-run: Lead sinh brief đúng schema; `slp-verify` bắt được 1 diff cố ý vượt scope + 1 diff đụng `merchant_id` + 1 diff sửa `.claude/`; `slp-conformance` pass toàn bộ |
| P3 | Skill mới `slp-supervisor` + `slp-lead`, harvest `schemas/`, `adapters/`, `bench/`, `tests/` từ `paseo-be-conductor` → **xác nhận với user rồi xoá repo cũ** (repo không có remote, có thay đổi chưa commit) | skill mới chạy đủ luồng; không còn reference tới conductor cũ |
| P4 | Pilot 1 task thật (≤ 3 slice), chạy cả Lead-direct và Supervisor | metrics mục 7b; so với chạy 1 agent Sonnet làm cả task |

### 7b. Metrics theo role (ghi vào `report.md` → gom `metrics.jsonl`)
| Metric | Theo | Vì sao |
|---|---|---|
| input / output / thinking token, cost ước tính | từng role (Supervisor, Lead, Builder, Checker, Reviewer) | Cursor 2026: planner có thể chiếm ~2/3 hóa đơn dù workers dùng ≥ 69% token |
| số vòng fix loop, số lần lên Sonnet fallback | slice | builder rẻ có thật sự rẻ không |
| `slp-verify` fail theo loại (test / scope / hard gate / escape) | slice | đo "làm hộ" và rủi ro |
| finding bị `slp-evidence` loại / tổng finding | checker | đo ảo giác reviewer |
| finding thật bị Final Reviewer bắt mà Checker bỏ sót | workstream | chất lượng checker |
| wall-clock / slice, / workstream | — | chi phí điều phối 3 tầng vs 2 tầng |
Baseline so sánh: cùng task chạy 1 agent Sonnet (hoặc Lead-direct). Chưa có bằng chứng công khai rằng 3 tầng hơn 2 tầng cho coding → P4 phải trả lời.

## 8. Câu hỏi mở

1. ~~Tên tool `paseoTools.disabledTools`~~ — đã trả lời (§5 Wiring).
2. ~~Cách Paseo spawn từng provider~~ — đã trả lời (§5 Wiring); 4 điểm unknown chuyển thành probe conformance.

### P3 log + quyết định thiết kế (2026-10-14)
Skill nháp: `skills/slp-lead/` (SKILL.md 152 dòng + references/profiles.md, prompts.md), `skills/slp-supervisor/SKILL.md` (103 dòng). Chưa cài.
Review phát hiện 10 điểm; quyết định (Opus):
1. `.slp/` không ai exclude được → `slp-verify --init <worktree>` ghi `.slp/` vào `info/exclude` của repo (deterministic, Lead được chạy).
2. Lead không tạo/đổi worktree được → Lead dùng Paseo `create_workspace` (worktree) cho workstream, spawn Builder vào workspace đó; Lead không cần ngồi trong worktree.
3. Song song khác worktree → guard cho Lead ghi `.slp/**` của **mọi worktree cùng repo** (so `git-common-dir`), không chỉ cwd. Mặc định vẫn tuần tự.
4. Không có tool `wait` → dùng `notifyOnFinish` (sửa §5).
5. Paseo tools của Lead: giữ `create_workspace`, `cancel_agent`, `list_*` (cần cho 2); §5 cập nhật theo policy thật.
6. Builder Sonnet thiếu checker khác họ → thêm role `codex-checker` (provider `slp-codex-checker`, sandbox read-only, contract review.json) + profile `slp-checker-codex`.
7. §4 brief bổ sung `base_ref` (bắt buộc), `parallel_group` (tuỳ chọn) — schema là nguồn chuẩn.
8. Checker read-only không ghi được review.json → checker/reviewer Claude chuyển **write = slp-only** (hook) + Bash read-only, mode `bypassPermissions` thay `plan` (conformance @bypass đã chứng minh hook chặn đúng); Codex checker/advisor: Lead lưu JSON từ câu trả lời cuối (yêu cầu JSON gọn).
9. `repos/<repo>.yaml` chưa có → mọi slice `unconfigured` (cố ý, không tự pass); cấu hình khi chọn repo pilot.
10. Builder: Flash → override provider `antigravity` (đang làm); DeepSeek: mode `slp-builder` đã nhận, chỉ còn chờ quota.
- **Đã làm (2026-10-14, chiều):** decisions 1–8 implement xong; 69 test pass; conformance 6 role PASS (`conformance-2026-10-14-1842.json`). Skills `slp-supervisor`/`slp-lead` symlink vào `~/.claude/skills`, global `user-invocable-only`, bật `on` trong settings của đúng role. Profile: 4 checker/reviewer Claude → `bypassPermissions` (hook giới hạn ghi `.slp/`), thêm `slp-checker-codex`. Subagent vá thêm: guard từng strip `VAR=`/`env`/`sudo` → Lead chạy được `SLP_REPOS_DIR=… slp-verify` (đã chặn cho role strict); `git -C` xác định HEAD cho kiểm branch bảo vệ, cấm `--git-dir/--work-tree`.
- Còn lại: `repos/<repo>.yaml` (chờ repo pilot); `_default.yaml` hard-gate chờ team duyệt; OpenCode conformance (quota); checker chưa có `slp-validate`; MCP theo role; harvest + xoá `paseo-be-conductor`; Paseo giữ row workspace đã archive trong `~/.paseo/projects/workspaces.json` (không có API xoá hẳn).

### Antigravity + Codex gap log (2026-10-14)
- `agy-builder`: role + agent `~/.gemini/config/agents/slp-agy-builder.md` (cài bởi `slp-build --apply`), allowlist 8 tool (view/list/grep/find/write/replace/multi_replace/run_command). Conformance (chạy `agy` trực tiếp, cùng argv Paseo): marker, ghi, bash, tool trong allowlist, quote đúng rule tenant → PASS.
- **Chặn**: Paseo chỉ cho `extends` claude/codex/copilot/opencode/pi/omp/acp → KHÔNG tạo được provider `slp-agy-builder`; profile `slp-builder-flash` vẫn chạy không harness. Lựa chọn: override provider `antigravity` gốc, patch Paseo fork, hoặc giữ nguyên.
- Codex advisor: `-c features.multi_agent=false -c features.multi_agent_v2=false -c model_catalog_json=build/codex/catalog.json` (catalog bỏ trường `multi_agent_version` — field này tự chèn spawn tools bất kể feature flag). Conformance gpt-6-luna: read-only giữ, không còn spawn tools.
- Dọn: backup config chuyển từ `/tmp` sang `build/config-before-apply.json` (ghi đè mỗi lần); xoá 9 dòng mồ côi trong `~/.gemini/antigravity-cli/conversation_summaries.db`.

### P2 log (2026-10-14)
- Dựng xong: `bin/slp-verify` · `bin/slp-evidence` · `bin/slp-validate` · `lib/` · `schemas/` (6) · `templates/` (6) · `repos/_default.yaml` (DRAFT, chờ team duyệt) · `repos/TEMPLATE.yaml` · 9 role.md hoàn chỉnh (22–47 dòng) · `slp-conformance` dùng 1 scratch dir + `removeProject` (không còn rác).
- Test: 29/29 pass (unittest, fixture git repo). Lead conformance pass 2 lần (chạy được slp-verify/slp-evidence, Bash khác bị chặn, ghi `.slp/` được).
- Review fix (Opus): `--repo-config` chỉ chấp nhận file dưới `paseo-slp/repos/` (trước: chỉ cấm trong worktree → builder có thể đặt YAML ở /tmp để chạy lệnh dưới quyền Lead). `SLP_REPOS_DIR` chỉ dùng cho test; guard của Lead không cho prefix env.
- Giới hạn đã biết: hard-gate tenant chỉ bắt dòng **bị xoá/sửa** chứa `merchant_id`; query MỚI thiếu filter không bắt được → việc của Checker. `writer_conflict` chỉ thấy brief có trong `.slp/` của worktree đang verify. `slp-validate` chưa nằm trong allowlist của Lead.

### P1 log (2026-10-14)
- Dựng xong: `roles/` (9 role: role.md + policy.yaml) · `bin/slp-build` · `bin/slp-conformance` · `hooks/slp-guard.py` (57 case test) · 9 provider `slp-*` đã apply (additive, profile chưa chuyển).
- Conformance `conformance/conformance-2026-10-14-1738.json`: Claude roles + codex-advisor + pi-builder PASS (ghi/bash phán theo filesystem). OpenCode UNVERIFIED (hết quota + wiring lỗi). Antigravity chưa làm.
- Trả lời unknown: (a) `--append-system-prompt` của mình **bị append của Paseo đè** → role prompt đi qua hook `SessionStart` (OK cả 6 role Claude). (b) `--settings` có hiệu lực (skillOverrides, deny, hook). (c) Codex `-c sandbox_mode="read-only"` được tôn trọng khi không set modeId; cần thêm `-c model_instructions_file=<role.md>` vì Paseo đè `developer_instructions` (thay base instructions của Codex).
- Review fix (Opus): PreToolUse guard thêm `|| exit 2` → fail-closed cả khi thiếu interpreter.
- Đã chuyển 9 profile sang provider `slp-*` (advisor-codex bỏ modeId). Builder Flash vẫn ở `antigravity`.
- OpenCode: `slp-build --apply` cài agent global `~/.config/opencode/agents/slp-builder.md` (generated); provider `slp-opencode-builder` bỏ env; profile `slp-builder-deepseek` → provider `slp-opencode-builder`, modeId `slp-builder` (Paseo đã thấy mode). Conformance chờ quota.
- Rác đã dọn: 13 workspace `slp-conf-*` (mỗi probe `paseo run --cwd <tmp>` sinh 1 workspace; CLI không có lệnh archive workspace). **TODO P2**: `slp-conformance` chạy mọi probe trong 1 scratch dir cố định (file probe đặt tên theo role) để chỉ có 1 workspace, tái dùng mỗi lần chạy; cuối run gọi daemon `project.remove.request` (client `removeProject`) cho project scratch nếu có tạo mới. 13 project `slp-conf-*` đã xoá qua API này.
- Gap: OpenCode — Paseo 0.11.2 dùng server singleton, bỏ qua `OPENCODE_CONFIG_CONTENT` per-provider → modeId `slp-builder` bị từ chối; Codex advisor vẫn thấy tool `spawn_agent`; MCP chưa tách theo role; skill Codex/Pi chưa kiểm soát.

### P0 log (2026-10-14)
- Đã đổi trong `~/.paseo/config.json` + `paseo reload`: `be-checker-codex` → tên "BE Checker · Opus", provider `be-claude-worker`, mode `plan` (trước: `claude` + `bypassPermissions` + còn Paseo tools). `be-advisor-codex` → provider `be-codex-worker` (tắt Paseo tools). Tên/notes `be-builder-codex-t2`, `be-builder-flash`, `be-builder-sonnet` khớp provider/model thật.
- Gap còn lại → P1: Codex không có mode read-only trong Paseo (cần `--sandbox read-only`); builder Claude (Sonnet fallback) chưa có checker khác họ + read-only.
- `be-conductor` vẫn trỏ skill `be-paseo-conductor` đã gỡ symlink → thay bằng `slp-supervisor` ở P3.
- Sau P0: toàn bộ profile đổi sang `slp-*` (giữ provider cũ tới P1), thêm `slp-lead`, `slp-integration-reviewer`, `slp-builder-deepseek` (opencode-go/deepseek-v4.1-flash); Supervisor thinking high → medium. Profile `be-*` cũ đã bị thay.

### Đã chốt (2026-10-14)
- Lead nói chuyện được với user; task đơn giản user chat thẳng Lead (Lead-direct, không Supervisor). Dưới Supervisor, đổi scope phải ghi vào `report.md`.
- Harness riêng cho mọi provider (tối ưu chi phí multi-model) → `roles/` + `slp-build`.
- Lead không tự chạy test bằng LLM; dùng script `slp-verify` (deterministic); Checker chỉ triage khi fail.
- Final Reviewer ở Lead / workstream; Supervisor chỉ nhận kết quả (+ Integration Reviewer khi chung contract).
- Skill mới `slp-supervisor`, `slp-lead`; xoá `paseo-be-conductor` sau khi harvest (P3, có xác nhận).

## 9. Nguồn tham khảo (research 2026-10-14)
- Anthropic multi-agent research system (2025-06-13): https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic evals for agents (2026-01-09): https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic advisor strategy (2026-04-09): https://claude.com/blog/the-advisor-strategy
- Cognition — Don't build multi-agents (2025-06-12): https://cognition.com/blog/dont-build-multi-agents · update (2026-04-22): https://cognition.com/blog/multi-agents-working
- Cursor — scaling agents (2026-01-14): https://cursor.com/blog/scaling-agents · cost study (2026-07-26): https://the-decoder.com/cursors-agent-swarm-suggests-cheaper-models-can-handle-most-coding-when-frontier-models-plan-the-work/
- Factory Missions: https://factory.ai/news/missions-architecture
- Google Research — scaling agent systems (2026-01-28): https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/
- MAST failure taxonomy: https://arxiv.org/abs/2503.13657
- Claude Code subagents / agent teams: https://code.claude.com/docs/en/sub-agents · https://code.claude.com/docs/en/agent-teams
- Codex subagents: https://learn.chatgpt.com/docs/agent-configuration/subagents · OpenCode agents: https://opencode.ai/docs/agents/
- Pillar — sandbox escapes (2026-07): https://pillar.security/blog/the-week-of-sandbox-escapes
