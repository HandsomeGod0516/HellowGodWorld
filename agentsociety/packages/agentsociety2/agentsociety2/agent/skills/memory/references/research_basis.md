# Memory — Research Basis

本技能把“長時記憶維護”拆成兩層：寫入（LLM 決定哪些值得記）與維護（指令碼按模型衰減、檢索啟用與清理）。

## 採用的遺忘曲線：Ebbinghaus Forgetting Curve（指數衰減形式）

我們使用指數衰減作為可控、可解釋、可實現的 baseline：

\[
R(t)=\exp\left(-\frac{t}{S \cdot k}\right)
\]

- \(R(t)\)：保留度（retention），範圍 \([0,1]\)
- \(t\)：距離該記憶建立的 tick 數
- \(S\)：強度係數（strength），控制衰減速度（預設建議 \(S=100\) ticks）
- \(k\)：重要性乘子（importance multiplier），例如：high=1.5、medium=1.0、low=0.5

### 為什麼選指數形式

- **實現簡單**：只需要 tick 差與少量引數
- **可解釋**：衰減速度與“強度/重要性”可直覺調參
- **穩定**：不會出現奇異點或不可控的長尾

### 強化（rehearsal / retrieval practice）

被檢索或反覆提及的記憶會更不容易忘。指令碼可用兩種簡單策略之一：

1) **加性強化**：訪問一次使 \(R\leftarrow \min(0.95, R+\Delta)\)
2) **等效時間回撥**：訪問一次使 \(t\leftarrow \max(0, t-\tau)\)

倉庫指令碼預設採用簡單可控的加性強化（見 `../scripts/memory_maintenance.py`）。

## ACT-R Base-Level Learning：多次經歷/回憶的疊加

只用“建立時間”會低估社會模擬的一個關鍵事實：一個人反覆見到的同事、路線、承諾、衝突，即使第一次發生很久以前，也應當比一次性事件更容易被想起。

因此維護指令碼額外計算 ACT-R 風格的 base-level activation：

\[
B_i=\ln\left(\sum_j t_j^{-d}\right)
\]

- \(B_i\)：記憶塊 \(i\) 的基礎啟用。
- \(t_j\)：距離第 \(j\) 次呈現/檢索的 tick 間隔，最小按 1 處理，避免除零。
- \(d\)：衰減引數，預設 \(0.5\)。
- 多次呈現以求和方式疊加，因此重複經歷會形成更高啟用。

指令碼實現中還加了兩個模擬友好的項：

\[
B_i' = B_i + \ln(k) + 0.08 \cdot \min(10, access\_count)
\]

- \(k\)：重要性乘子，沿用 high=1.5、medium=1.0、low=0.5。
- `access_count`：檢索次數，用小幅 bonus 表示 retrieval practice。

再用 logistic 函式把啟用轉成可解釋機率：

\[
P(retrieve)=\frac{1}{1+\exp(-(B_i' - \theta))}
\]

最後：

\[
retention=\max(R_{Ebbinghaus}, P(retrieve))
\]

這樣做的好處是：

- 單次低價值事件仍會自然淡出；
- 被反覆遇到的人、地點、規則、承諾會更穩定；
- 模擬中“熟悉感”和“社會連續性”不必完全依賴 LLM 臨場回憶。

## 引數建議

| 引數 | 預設 | 含義 | 調參方向 |
|------|------|------|----------|
| `AGENT_MEMORY_STRENGTH` | `100` | Ebbinghaus 指數衰減強度 | tick 很短時調大 |
| `AGENT_MEMORY_ACTR_DECAY` | `0.5` | ACT-R 呈現項的冪律衰減 | 想讓重複經歷更快淡出時調大 |
| `AGENT_MEMORY_RETRIEVAL_THRESHOLD` | `-2.5` | retrieval probability 的閾值 | 想更嚴格保留時調高 |
| `AGENT_MEMORY_MAX_ENTRIES` | `1000` | 檔案容量上限 | 大規模模擬按成本調節 |

## 參考

- Hermann Ebbinghaus. *Memory: A Contribution to Experimental Psychology* (1885).
- Anderson, J. R. & Schooler, L. J. (1991). Reflections of the environment in memory.
- ACT-R base-level learning equation: repeated presentations add as power-law terms; common decay default \(d=0.5\).
- Roediger, H. L. & Karpicke, J. D. (2006). Test-enhanced learning: taking memory tests improves long-term retention.
- 現代 retrieval practice 綜述可用於解釋“檢索強化”。
