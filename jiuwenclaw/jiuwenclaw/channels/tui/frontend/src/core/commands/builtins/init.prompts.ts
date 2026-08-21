import type { CommandContext } from "../types.js";

export type ScopeKey = "project" | "personal" | "both";

export interface ExistingFiles {
  jiuwenclawMd: boolean;
  jiuwenclawLocalMd: boolean;
  claudeMd: boolean;
  claudeLocalMd: boolean;
  agentsMd: boolean;
  openjiuwenMd: boolean;
  cursorRules: boolean;
  copilotInstructions: boolean;
}

export interface BuildInitPromptArgs {
  rootDir: string;
  scopeKey: ScopeKey;
  language: "zh" | "en";
  existing: ExistingFiles;
}

// ---------------------------------------------------------------------------
// Language resolution
// ---------------------------------------------------------------------------

export function resolveLanguage(_ctx: CommandContext): "zh" | "en" {
  // 當前方案：best-effort from LANG env; 後續可讀 config.
  const lang =
    typeof process !== "undefined" ? (process.env.LANG ?? "") : "";
  return /^zh/i.test(lang) || /CN$/i.test(lang) ? "zh" : "en";
}

// ---------------------------------------------------------------------------
// Prompt builder
// ---------------------------------------------------------------------------

export function buildInitPrompt(args: BuildInitPromptArgs): string {
  return args.language === "zh" ? buildInitPromptZh(args) : buildInitPromptEn(args);
}

// ---------------------------------------------------------------------------
// English (authoritative source)
// ---------------------------------------------------------------------------

function buildInitPromptEn({ rootDir, scopeKey, existing }: BuildInitPromptArgs): string {
  const scopeLine = SCOPE_DESCRIPTION_EN[scopeKey];
  return `Set up a minimal JIUWENCLAW.md (team-shared) and optionally JIUWENCLAW.local.md (personal) for this repository.

These files are auto-loaded into every coding-mode session by ProjectMemoryRail, so they must be CONCISE — only include what the assistant would get wrong without them.

## CRITICAL Constraints (read first, do not violate)

1. **All file operations MUST use absolute paths rooted at: \`${rootDir}\`**
   Never use relative paths. When writing or editing, always construct \`${rootDir}/<filename>\`.
2. **Do NOT use the \`coding_memory_read\` / \`coding_memory_write\` / \`coding_memory_edit\` tools in this command.** Those are for session-level auto-memory, a different system. /init produces static project documents via the file write tools only.
3. **Existing files pre-detected in workspace root**:
  - JIUWENCLAW.md: ${yesNo(existing.jiuwenclawMd)} ${existing.jiuwenclawMd ? "— you MUST read it first, propose a diff, then use `ask_user` with `questions` to ask the user whether to apply. Example: `ask_user(query='Update JIUWENCLAW.md?', questions=[{question: 'JIUWENCLAW.md already exists. What would you like to do?', header: 'Update', options: [{label: 'Apply update', description: 'Merge the proposed changes into the existing file'}, {label: 'Skip (keep current)', description: 'Leave the file unchanged and continue'}], multi_select: false}])`. If user chooses 'Apply update', use Edit to apply the diff; if 'Skip', leave the file unchanged and continue. NEVER silently overwrite." : ""}
   - JIUWENCLAW.local.md: ${yesNo(existing.jiuwenclawLocalMd)} ${existing.jiuwenclawLocalMd ? "— propose additions via Edit only, never overwrite." : ""}
   - Legacy reference files (do NOT delete or rewrite; you may link to them): CLAUDE.md=${yesNo(existing.claudeMd)}, CLAUDE.local.md=${yesNo(existing.claudeLocalMd)}, AGENTS.md=${yesNo(existing.agentsMd)}, OPENJIUWEN.md=${yesNo(existing.openjiuwenMd)}, .cursorrules=${yesNo(existing.cursorRules)}, .github/copilot-instructions.md=${yesNo(existing.copilotInstructions)}
4. **When the explore sub-agent runs bash commands**, always prefix with \`cd ${rootDir} && ...\` or use \`git -C ${rootDir}\` — sub-agent CWD is not guaranteed to equal \`${rootDir}\`.
5. **Always prefer \`task_tool\` with \`subagent_type: "explore_agent"\` when it is available.** If \`task_tool\` is unavailable for this turn, silently FALL BACK to \`glob\` / \`grep\` / \`read_file\` / \`bash\` yourself.
6. **Default to a single \`task_tool\` / \`explore_agent\` call.** If the repository is clearly large, a monorepo, or one pass does not gather enough signal, you may split the work across multiple explore sub-agents; only parallelize when there is a clear benefit, to avoid duplicate scanning and noisy result merging.

## Step 1: Scope (already answered)

User chose: **${scopeKey}** — ${scopeLine}

## Step 2: Explore the codebase

Preferred path — invoke \`task_tool\` with:
\`\`\`
subagent_type: "explore_agent"
task_description: |
  Thoroughly explore the repository at ${rootDir}. Use "very thorough" exploration.
  Read these key files if present (use absolute paths):
    - Manifests: package.json, Cargo.toml, pyproject.toml, go.mod, pom.xml, build.gradle*, setup.py
    - Docs: README.*, CONTRIBUTING.*, ARCHITECTURE.*, docs/
    - Build/CI: Makefile, justfile, .github/workflows/*, .gitlab-ci.yml, azure-pipelines.yml
    - AI tool configs: JIUWENCLAW.md, CLAUDE.md, AGENTS.md, OPENJIUWEN.md,
                       .jiuwen/rules/*, .claude/rules/*, .cursor/rules/*,
                       .cursorrules, .github/copilot-instructions.md,
                       .windsurfrules, .clinerules, .mcp.json
    - Config: .jiuwen/settings*.json (read-only; do not rewrite)
  Detect and report back concisely:
    - Build / test / lint / format commands (especially non-standard ones)
    - Primary languages, frameworks, package manager
    - Project structure (monorepo, multi-module, single-package)
    - Code style rules differing from language defaults
    - Non-obvious gotchas, required env vars, workflow quirks
    - Branch / PR / commit message conventions
    - Run \`git -C ${rootDir} worktree list\` and mention if multiple worktrees exist
  Note anything you CANNOT figure out from code alone — these become interview questions.
\`\`\`

Fallback (no task_tool): do the same yourself with \`glob\` and \`read_file\`; focus on the manifest + README first, then Makefile / CI configs.

## Step 3: Fill gaps + build proposal

Gather info code can't answer. Use the \`ask_user\` tool with structured \`questions\` parameter.

The \`ask_user\` tool supports a \`questions\` parameter for presenting selectable options:
\`\`\`
ask_user(
  query="Brief description of what you're asking",
  questions=[
    {
      question: "The full question text",
      header: "ShortTag",
      options: [
        {label: "Option A", description: "What option A means"},
        {label: "Option B", description: "What option B means"},
      ],
      multi_select: false,
    }
  ]
)
\`\`\`

Use selectable options when they help clarify the question, or ask open-ended questions to gather free-form input. The user can always choose "Other" for custom input.

For scope \`project\` / \`both\`: ask about team practices —
  non-obvious commands, branch/PR conventions, env setup, testing quirks, common pitfalls.
  Skip items already obvious from README or manifests. Do not mark any answer as "recommended" — this is about the team's actual workflow.

For scope \`personal\` / \`both\`: ask about the user —
  role, familiarity with this codebase, sandbox URLs / accounts, communication preferences, specific tooling setup on their machine.

**Synthesize a proposal** combining Step 2 findings and Step 3 answers. Because skills and hooks are outside the current scope, ALL items become JIUWENCLAW.md notes (team) or JIUWENCLAW.local.md notes (personal). Present as a plain-text list, one line per item, grouped by target file. Ask for confirmation before proceeding.

**Build the preference queue** from the accepted proposal:
\`[{type: "note", target: "JIUWENCLAW.md" | "JIUWENCLAW.local.md", content: "..."}]\`
Steps 4–5 consume this queue.

## Step 4: Write JIUWENCLAW.md (if scope is project or both)

Target: \`${rootDir}/JIUWENCLAW.md\`

${existing.jiuwenclawMd ? "File EXISTS — read it, propose a merged diff, use `ask_user` with `questions` to get user confirmation (options: 'Apply update' / 'Skip (keep current)'), then apply via Edit if confirmed. DO NOT use Write to overwrite silently." : "File is absent — use Write to create it."}

Consume queue entries whose \`target == "JIUWENCLAW.md"\`.

**Content test**: for each candidate line, ask "Would removing this cause the assistant to make mistakes?" If no, cut.

**Include**:
- Build / test / lint / format commands the assistant can't guess
- Code style rules that deviate from language defaults
- Testing quirks (e.g., "run single test with \`pytest -k ...\`")
- Repo etiquette (branch naming, PR conventions, commit message style)
- Required env vars, setup steps
- Important parts from existing AI coding tool configs if they exist (AGENTS.md, .cursor/rules, .cursorrules, .github/copilot-instructions.md, .windsurfrules, .clinerules) — extract key rules, not just link to them
- Non-obvious gotchas, architectural decisions worth knowing
- A brief **See also** section. Use plain markdown links for short references, or \`@path/to/file\` includes when a longer source document should stay authoritative:
    ${legacyIncludesEn(existing)}

**Exclude**:
- File-by-file structure or component lists (assistant can discover)
- Standard language conventions (assistant already knows)
- Generic AI etiquette / prompt engineering advice
- Long inline reference material — link to it rather than inline
- Commands already obvious from manifests (e.g., "npm test")
- Frequently-changing information — reference the source with \`@path/to/doc.md\` so the latest version is always loaded
- Generic advice like "write clean code" or "handle errors" — only include specific, actionable rules

**Specificity rule**: "Use 2-space indentation in TypeScript" is better than "Format code properly."

**No invented sections**: Do not make up headings like "Common Development Tasks" or "Tips for Development" — only include information expressly found in files you read.

**Prefix** the file with:
\`\`\`
# JIUWENCLAW.md

This file provides guidance to JiuwenClaw (and any compatible AI coding assistant) when working with code in this repository.
\`\`\`

For monorepos: mention that subdirectory \`JIUWENCLAW.md\` is supported — ProjectMemoryRail walks up from cwd, so per-package docs are welcome.

For rule organization at team scale: suggest creating \`.jiuwen/rules/<topic>.md\` — these are auto-scanned, and may use frontmatter \`paths:\` to scope rules by the current working subtree / workspace.

## Step 5: Write JIUWENCLAW.local.md (if scope is personal or both)

Target: \`${rootDir}/JIUWENCLAW.local.md\`

${existing.jiuwenclawLocalMd ? "File EXISTS — propose additions via Edit, never overwrite." : "File is absent — use Write to create it."}

Consume queue entries whose \`target == "JIUWENCLAW.local.md"\`.

Include: user's role, familiarity, personal URLs / accounts, communication preferences, tool setup specific to the user's machine.

**After writing**, idempotently update \`${rootDir}/.gitignore\`:
  1. Read \`.gitignore\` if it exists (use absolute path).
  2. Check whether each of the two lines below is already present (exact line match).
  3. Append only the missing ones:
       - \`JIUWENCLAW.local.md\`
       - \`.jiuwen/settings.local.json\`
  4. If \`.gitignore\` does not exist, create it with those two lines.

## Step 6: Summary

Briefly recap which files were written and the 3–5 most important items in each.

Remind the user:
- These files are auto-loaded into every coding session by ProjectMemoryRail.
- They're a starting point — feel free to edit by hand; changes take effect next turn.
- Re-run \`/init\` anytime to refresh based on new findings.

Then suggest optimizations as a short checklist, only those relevant to this repo:
- If tests are missing / sparse: suggest setting up a framework so the assistant can verify its own changes.
- If no formatter / lint config was found: suggest adding one with a one-line reason.
- If Step 2 found legacy AI config files (CLAUDE.md, AGENTS.md, etc.) not referenced in JIUWENCLAW.md: suggest consolidating via plain links or follow-up cleanup.
- **Always include**: "Run \`/compact\` after reviewing to trim this init session from history."
`;
}

// ---------------------------------------------------------------------------
// Chinese
// ---------------------------------------------------------------------------

function buildInitPromptZh({ rootDir, scopeKey, existing }: BuildInitPromptArgs): string {
  const scopeLine = SCOPE_DESCRIPTION_ZH[scopeKey];
  return `為本倉庫生成一份最小可用的 JIUWENCLAW.md（團隊共享）與可選的 JIUWENCLAW.local.md（個人私有）。
這些檔案會被 ProjectMemoryRail 自動注入到每一輪 coding 模式會話的 system prompt，因此必須**精簡** —— 只寫"不寫就會出錯"的資訊。

## 關鍵約束（必讀，不可違反）

1. **所有檔案操作必須使用絕對路徑，根為：\`${rootDir}\`**
   永遠不要用相對路徑。寫入或編輯時總是構造 \`${rootDir}/<檔名>\`。
2. **禁止使用 \`coding_memory_read\` / \`coding_memory_write\` / \`coding_memory_edit\` 工具。** 那是會話級自動記憶，和 /init 是兩套系統。/init 只透過檔案寫入工具產出靜態專案文件。
3. **工作區根目錄現有檔案（已預探測）**：
   - JIUWENCLAW.md：${yesNoZh(existing.jiuwenclawMd)} ${existing.jiuwenclawMd ? "—— 必須先讀取、生成 diff，然後用 \`ask_user\` 的 \`questions\` 引數讓使用者選擇。示例：\`ask_user(query='更新 JIUWENCLAW.md？', questions=[{question: 'JIUWENCLAW.md 已存在，你想怎麼處理？', header: '更新', options: [{label: '應用更新', description: '把提議的變更合併到現有檔案'}, {label: '跳過（保留當前）', description: '保持檔案不變，繼續後續步驟'}], multi_select: false}])\`。若使用者選「應用更新」，用 Edit 執行 diff；若選「跳過」，保持檔案不變繼續。嚴禁靜默覆蓋。" : ""}
   - JIUWENCLAW.local.md：${yesNoZh(existing.jiuwenclawLocalMd)} ${existing.jiuwenclawLocalMd ? "— 只能透過 Edit 追加，不要覆蓋。" : ""}
   - 遺留參考檔案（不要刪改，可用 markdown 連結引用）：CLAUDE.md=${yesNoZh(existing.claudeMd)}, CLAUDE.local.md=${yesNoZh(existing.claudeLocalMd)}, AGENTS.md=${yesNoZh(existing.agentsMd)}, OPENJIUWEN.md=${yesNoZh(existing.openjiuwenMd)}, .cursorrules=${yesNoZh(existing.cursorRules)}, .github/copilot-instructions.md=${yesNoZh(existing.copilotInstructions)}
4. **子代理 bash 命令必須加字首**：\`cd ${rootDir} && ...\` 或用 \`git -C ${rootDir}\`，因為子代理的 CWD 不保證等於 \`${rootDir}\`。
5. **只要可用，始終優先使用 \`task_tool\` 且 \`subagent_type: "explore_agent"\`。** 若本輪工具列表裡沒有 \`task_tool\`，就靜默降級為用 \`glob\` / \`grep\` / \`read_file\` / \`bash\` 自行探索。
6. **預設只發起一次 \`task_tool\` / \`explore_agent\` 呼叫。** 若倉庫明顯較大、為 monorepo，或單次探索資訊不足，可按需拆分多個 explore 子代理；只有在確有收益時才併發，避免重複掃描與結果合併噪音。

## 步驟 1：範圍（已確定）

使用者選擇：**${scopeKey}** — ${scopeLine}

## 步驟 2：探索程式碼庫

首選：呼叫 \`task_tool\`，引數：
\`\`\`
subagent_type: "explore_agent"
task_description: |
  徹底探索倉庫 ${rootDir}，請求 "very thorough" 級別。
  若存在請讀取（用絕對路徑）：
    - 清單：package.json, Cargo.toml, pyproject.toml, go.mod, pom.xml, build.gradle*, setup.py
    - 文件：README.*, CONTRIBUTING.*, ARCHITECTURE.*, docs/
    - 構建/CI：Makefile, justfile, .github/workflows/*, .gitlab-ci.yml, azure-pipelines.yml
    - AI 配置：JIUWENCLAW.md, CLAUDE.md, AGENTS.md, OPENJIUWEN.md,
              .jiuwen/rules/*, .claude/rules/*, .cursor/rules/*,
              .cursorrules, .github/copilot-instructions.md,
              .windsurfrules, .clinerules, .mcp.json
    - 配置：.jiuwen/settings*.json（只讀，不要重寫）
  簡潔地彙報以下內容：
    - 構建/測試/lint/format 命令（特別是非標準的）
    - 主要語言、框架、包管理器
    - 專案結構（monorepo / 多模組 / 單包）
    - 與語言預設不同的程式碼風格規則
    - 不易察覺的坑、必需環境變數、工作流習慣
    - 分支 / PR / commit message 約定
    - 執行 \`git -C ${rootDir} worktree list\`，若有多 worktree 請說明
  對於從程式碼無法推斷的問題，記錄下來作為後續的訪談問題。
\`\`\`

無 task_tool 時的兜底：用 \`glob\` + \`read_file\` 自己做同樣的事，先看清單和 README，再看 Makefile / CI 配置。

## 步驟 3：補齊資訊 + 生成提案

收集程式碼無法回答的問題。用 \`ask_user\` 工具的 \`questions\` 引數提供可選項：

\`\`\`
ask_user(
  query="簡要說明你在問什麼",
  questions=[
    {
      question: "完整的問題文字",
      header: "短標籤",
      options: [
        {label: "選項 A", description: "選項 A 的含義"},
        {label: "選項 B", description: "選項 B 的含義"},
      ],
      multi_select: false,
    }
  ]
)
\`\`\`

根據問題性質選擇選項式提問或直接輸入式提問；使用者始終可以選擇「其他」進行自定義輸入。

對 \`project\` / \`both\` 範圍：詢問團隊實踐 —
  非顯而易見的命令、分支 / PR 約定、環境初始化、測試習慣、常見坑位。
  README 或清單裡已經寫清楚的就別問。**不要**給任何選項標記"推薦" —— 這是團隊實際做法，不是建議。

對 \`personal\` / \`both\` 範圍：詢問使用者 —
  角色、對本倉庫的熟悉度、沙箱 URL / 賬號、溝通偏好、本機工具鏈特殊設定。

**合成提案**：把步驟 2 的發現和步驟 3 的回答整合。當前方案不支援 Skills 和 Hooks，所有條目一律歸為 JIUWENCLAW.md（團隊）或 JIUWENCLAW.local.md（個人）的記錄項。用純文字列表呈現，按目標檔案分組。請求使用者確認後再寫檔案。

**構造偏好佇列**：
\`[{type: "note", target: "JIUWENCLAW.md" | "JIUWENCLAW.local.md", content: "..."}]\`
後續寫檔案步驟會消費此佇列。

## 步驟 4：寫 JIUWENCLAW.md（當範圍是 project 或 both）

目標：\`${rootDir}/JIUWENCLAW.md\`

${existing.jiuwenclawMd ? "檔案已存在 —— 先讀取，生成合並 diff，用 \`ask_user\` 的 \`questions\` 引數獲取使用者確認（選項：「應用更新」 / 「跳過（保留當前）」），確認後用 Edit 應用。絕不要用 Write 靜默覆蓋。" : "檔案不存在 —— 用 Write 建立。"}

消費佇列中 \`target == "JIUWENCLAW.md"\` 的條目。

**內容篩選測試**：對每行候選，自問"去掉這行會不會讓助手犯錯？" 不會就刪掉。

**應包含**：
- 助手猜不出的構建 / 測試 / lint / format 命令
- 偏離語言預設的程式碼風格規則
- 測試習慣（例如"用 \`pytest -k 'x'\` 跑單測"）
- 倉庫規矩（分支命名、PR 約定、commit message 風格）
- 必需環境變數、初始化步驟
- 從已有的 AI 工具配置檔案中提取重要內容（CLAUDE.md、AGENTS.md、.cursorrules、.github/copilot-instructions.md、.windsurfrules、.clinerules 等） —— 提取關鍵規則，而非只留連結引用
- 不易察覺的坑、值得知道的架構決策
- 簡短的 **See also** 段落。短引用可用普通 markdown 連結；若希望保留長文件作為權威來源，可用 \`@path/to/file\` 引用：
    ${legacyIncludesZh(existing)}

**不應包含**：
- 逐檔案 / 逐元件的結構清單（助手可以自己發現）
- 語言的標準約定（助手已經知道）
- 通用 AI 禮儀 / prompt 工程建議
- 長篇參考材料 —— 用連結引用而非內聯
- 清單中顯而易見的命令（比如"npm test"）
- 頻繁變化的資訊 —— 用 \`@path/to/doc.md\` 引用源頭，確保每次載入的都是最新版本
- 通用建議如"寫乾淨程式碼"或"處理好錯誤" —— 只寫具體、可執行的規則

**具體性原則**："TypeScript 用 2 空格縮排"比"程式碼要格式規範"好。

**禁止虛構段落**：不要自創"常見開發任務"或"開發技巧"之類的標題 —— 只收錄你從檔案中實際讀到的資訊。

**檔案開頭**統一加：
\`\`\`
# JIUWENCLAW.md

This file provides guidance to JiuwenClaw (and any compatible AI coding assistant) when working with code in this repository.
\`\`\`

對 monorepo：說明支援子目錄放獨立的 \`JIUWENCLAW.md\` —— ProjectMemoryRail 從 cwd 向上遍歷載入。

對團隊規模較大的專案：建議把按主題拆分的規則放到 \`.jiuwen/rules/<topic>.md\` —— 當前執行時會自動載入這些規則，並支援用 \`paths:\` frontmatter 按當前工作目錄 / workspace 所在子樹限定作用域。

## 步驟 5：寫 JIUWENCLAW.local.md（當範圍是 personal 或 both）

目標：\`${rootDir}/JIUWENCLAW.local.md\`

${existing.jiuwenclawLocalMd ? "檔案已存在 —— 透過 Edit 追加內容，不要覆蓋。" : "檔案不存在 —— 用 Write 建立。"}

消費佇列中 \`target == "JIUWENCLAW.local.md"\` 的條目。

包含：使用者的角色、對倉庫的熟悉程度、個人 URL / 賬號、溝通偏好、本機特有工具鏈配置。

**寫完後冪等更新** \`${rootDir}/.gitignore\`：
  1. 若 \`.gitignore\` 存在先讀取（用絕對路徑）；
  2. 檢查下面兩行是否已存在（整行精確匹配）；
  3. 僅追加缺失的：
       - \`JIUWENCLAW.local.md\`
       - \`.jiuwen/settings.local.json\`
  4. 若 \`.gitignore\` 不存在，就建立並寫入這兩行。

## 步驟 6：總結

簡要回顧寫了哪些檔案，每個檔案裡 3-5 條最重要的內容。

提醒使用者：
- 這些檔案會被 ProjectMemoryRail 自動載入到每一輪 coding 會話。
- 是起點 —— 可以手工編輯，下一輪就生效。
- 隨時可以再跑 \`/init\` 基於新發現重新生成。

然後給一個短清單（只寫與當前倉庫相關的）：
- 若測試缺失 / 稀疏：建議引入測試框架，助手才能自證修改。
- 若沒有 formatter / lint 配置：建議新增，並說明一行理由。
- 若步驟 2 發現了 JIUWENCLAW.md 中未引用的遺留 AI 配置檔案（CLAUDE.md、AGENTS.md 等）：建議以普通連結方式提示使用者後續合併。
- **總是包含**："檢查完後執行 \`/compact\` 可把這段初始化會話從歷史中精簡掉。"
`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SCOPE_DESCRIPTION_EN: Record<ScopeKey, string> = {
  project: "write only JIUWENCLAW.md (run Step 4).",
  personal: "write only JIUWENCLAW.local.md (run Step 5).",
  both: "write both files (run Step 4 and Step 5).",
};

const SCOPE_DESCRIPTION_ZH: Record<ScopeKey, string> = {
  project: "只寫 JIUWENCLAW.md（執行步驟 4）。",
  personal: "只寫 JIUWENCLAW.local.md（執行步驟 5）。",
  both: "兩份都寫（步驟 4 和步驟 5 都執行）。",
};

function yesNo(b: boolean): string {
  return b ? "EXISTS" : "absent";
}

function yesNoZh(b: boolean): string {
  return b ? "存在" : "不存在";
}

function legacyIncludesEn(existing: ExistingFiles): string {
  // 當前方案：不用 @path 展開；寫普通 markdown 連結
  const parts: string[] = [];
  if (existing.claudeMd) parts.push("[CLAUDE.md](./CLAUDE.md)");
  if (existing.agentsMd) parts.push("[AGENTS.md](./AGENTS.md)");
  if (existing.openjiuwenMd) parts.push("[OPENJIUWEN.md](./OPENJIUWEN.md)");
  if (existing.cursorRules) parts.push("[.cursorrules](./.cursorrules)");
  if (existing.copilotInstructions)
    parts.push(
      "[.github/copilot-instructions.md](./.github/copilot-instructions.md)",
    );
  return parts.length
    ? `"See also: ${parts.join(", ")}."`
    : `"(No legacy AI config files detected.)"`;
}

function legacyIncludesZh(existing: ExistingFiles): string {
  const parts: string[] = [];
  if (existing.claudeMd) parts.push("[CLAUDE.md](./CLAUDE.md)");
  if (existing.agentsMd) parts.push("[AGENTS.md](./AGENTS.md)");
  if (existing.openjiuwenMd) parts.push("[OPENJIUWEN.md](./OPENJIUWEN.md)");
  if (existing.cursorRules) parts.push("[.cursorrules](./.cursorrules)");
  if (existing.copilotInstructions)
    parts.push(
      "[.github/copilot-instructions.md](./.github/copilot-instructions.md)",
    );
  return parts.length
    ? `"另見：${parts.join("、")}。"`
    : `"（未探測到遺留 AI 配置檔案。）"`;
}
