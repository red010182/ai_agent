# 半導體製程疑難雜症智能客服 Agent — 系統規格

## 專案概述

一個部署於公司內網的智能客服 Agent，協助工程師查詢半導體製程疑難雜症 SOP，
透過對話引導工程師逐步排查問題，並在需要時執行資料庫 SELECT 查詢。

---

## 技術環境

- **執行環境**：公司內網，無法連外網
- **LLM**：公司內部部署，OpenAI 相容格式（base_url 可設定）
- **LLM 能力**：中等（8bit 量化模型，例如 Qwen 235B-A22B Q8 或同等級）
- **資料庫**：公司內部 DB（SQL 方言以實際環境為準，預設 PostgreSQL）
- **SQL 權限**：唯讀，只允許 SELECT
- **Vector Search**：Qdrant（本地部署）
- **互動方式**：Web Chat UI + FastAPI 後端，支援多聊天室並行
- **回覆方式**：SSE（Server-Sent Events）串流逐字顯示
- **Session 持久化**：記憶體（重啟清空），後續可升級為 Redis

---

## 目錄結構

```
project/
├── spec.md
├── CLAUDE.md
├── main.py                  # FastAPI app 入口
├── api/
│   ├── __init__.py
│   ├── routes.py            # HTTP 路由（/chat、/sessions）
│   └── sse.py               # SSE 串流回覆
├── frontend/                # Vite + React + TypeScript + Ant Design
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── ChatSidebar.tsx
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── ThinkingBlock.tsx
│   │   │   ├── SqlConfirmCard.tsx
│   │   │   ├── ParamFormCard.tsx    # 缺失參數表單
│   │   │   ├── ClarifyCard.tsx      # 反問選項卡片（含自由輸入）
│   │   │   └── SqlRecord.tsx
│   │   ├── hooks/
│   │   │   ├── useSession.ts
│   │   │   └── useSSE.ts
│   │   ├── store/
│   │   │   └── chatStore.ts
│   │   └── types.ts
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
├── agent/
│   ├── __init__.py
│   ├── session.py
│   ├── sop_loader.py
│   ├── vector_search.py     # 索引所有 case（無 is_entry 區別）
│   ├── router.py            # Vector Search + LLM 候選選擇
│   ├── llm_client.py
│   ├── sql_executor.py
│   └── param_extractor.py
├── sop/
│   ├── _index.md
│   ├── productivity_lost.md
│   └── ...
├── config.py
└── requirements.txt
```

---

## SOP 文件格式規範

### Front Matter 規範

整份 SOP 文件頂部一個 front matter，包含所有 case 的 metadata：

```yaml
---
scenario: productivity_lost
cases:
  - case_id: case_1
    title: Tool Scanner Lost
    keywords:
      - scanner lost
      - tool offline
      - productivity lost
    jumps_to: [case_2, case_12]

  - case_id: case_2
    title: Scanner Lost + Foup 未派滿
    keywords:
      - scanner lost
      - foup exchanger
      - 未派滿
    jumps_to: [case_6, case_7]

  - case_id: case_12
    title: Foup Exchanger 已派滿
    keywords:
      - foup exchanger
      - 已派滿
    jumps_to: []
---
```

**所有 case 都填 keywords**，因為每個 case 都可以是 entry point。
`jumps_to` 只能引用同一份 SOP 檔案內的 case_id。

### 正文結構規範

```markdown
## case N

### symptom
（此 case 的觸發條件，可能與其他 case 的 symptom 重疊）

### problem_to_verify
（需要向用戶釐清或驗證的核心問題）
（若不需要提問則寫 omit）

### how_to_verify
（驗證方法，可包含文字說明、GUI 操作、SQL 查詢、以及跳轉邏輯）

執行以下 SQL：
```sql
SELECT count(*) FROM foup_schedule
WHERE equipment_id = &equipment_id AND status = 'assigned'
```
- result > 0 → 走 case 12
- result = 0 → 走 case 2

### note
（補充說明、例外情況、背景知識，不含跳轉邏輯）
```

### SQL 佔位符規範

- 格式採用 `&param_name`（Oracle SQL*Plus 慣例，與工程師現有習慣一致）
- 例如：`SELECT * FROM table WHERE col = '&my_val'`
- param_name 使用底線分隔英文，例如：`&equipment_id`、`&lot_id`
- **Agent 不自行組合 SQL**，只執行 SOP template 填入參數後的結果
- SQL 結果的解讀方式由 `how_to_verify` 說明，不由 agent 推斷

---

## 路由設計

### 核心概念：所有 case 都是潛在 entry point

每個 case 的 symptom 都可能是用戶的起始描述，symptom 之間允許重疊。
Vector Search 索引所有 case，由 LLM 從候選結果中選出最符合的。

### 完整流程

```
用戶輸入症狀描述
    ↓
Vector Search（所有 case）→ top-3 候選
    ↓
所有候選 score < CONFIDENCE_THRESHOLD？
    ├── 是 → Fallback 閒聊模式
    └── 否 → LLM 候選選擇
               ↓
         比對用戶描述 vs 各候選的 symptom
               ↓
         LLM 信心度足夠？
         ├── 是 → 直接選出最符合的 case
         └── 否 → 列出候選讓用戶自行選擇
                  （candidates 的 symptom 差異不大時觸發）
               ↓
         載入該 case
         提取 how_to_verify 所有 sql block → 存入 sql_blocks
               ↓
         problem_to_verify != omit？
           ├── 是 → 向用戶提問，等待回答
           └── 否 → 直接進入 how_to_verify
               ↓
         how_to_verify 含 SQL？
           ├── 是 → 收集參數 → 確認 → 執行 → LLM 解讀結果決定跳轉
           └── 否 → LLM 直接依說明決定跳轉或結束
               ↓
         跳轉到同 SOP 的另一個 case，或流程結束
```

### LLM 候選選擇的 Prompt 設計

```
[用戶描述]
tool scanner lost，foup exchanger 看起來沒派滿

[候選 case 的 symptom]
case_1: tool scanner lost
case_2: tool scanner lost & foup exchanger 沒派滿
case_5: tool scanner lost & 系統A 異常

[任務]
根據用戶描述，選出 symptom 最符合的 case。
symptom 條件越多、越具體且與用戶描述吻合者優先。
只回傳 JSON，不得輸出其他內容。

輸出：{"chosen_case_id": "case_2", "reason": "用戶明確提到 foup exchanger 沒派滿"}
```

### 跳轉決策的 Prompt 設計

SQL 執行完畢後，LLM 解讀結果並決定跳轉：

```
[how_to_verify 內容]
- result > 0 → 走 case 12
- result = 0 → 走 case 2

[SQL 執行結果]
count: 3

[任務]
根據 SQL 結果與跳轉條件，決定下一步。
- 若 SQL 結果明確符合某個跳轉條件 → jump_to_case
- 若 SQL 結果無法對應任何跳轉條件，或需要更多資訊才能判斷 → clarify 反問用戶
- 確認超出 SOP 範圍才使用 human_handoff

reply_to_user 需包含兩件事：
1. 解讀 SQL 結果的意義（用繁體中文說明數值代表什麼）
2. 說明即將跳轉到哪個 case 以及原因（或反問用戶的問題）
只回傳 JSON。

輸出：{
  "next_action": "jump_to_case",
  "target_case_id": "case_12",
  "reason": "count=3 > 0",
  "reply_to_user": "查詢結果顯示共有 3 筆 foup 排程（count=3），表示 foup exchanger 已派滿。\n接下來進入 case 12 繼續排查派滿後的情況。"
}
```

---

## Agent 核心邏輯

### Session 狀態結構

```python
session = {
    "current_sop_file": "productivity_lost.md",
    "current_case_id": "case_1",

    # 路由模式
    "mode": "sop",            # "sop" | "fallback_chat"
    "fallback_reason": None,  # "no_results" | "low_confidence"

    # 對話歷史
    "conversation_history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
    ],

    # 參數收集
    "collected_params": {
        "equipment_id": "EQ-4721",
        "start_time": None,
    },

    # 已知狀態（跨 case 保留，用於 LLM 條件比對）
    "known_facts": [],

    # 已訪問的 case 記錄（防止無限循環）
    "visited_cases": {},   # {case_id: visit_count}
    "max_case_visits": 2,  # 同一 case 超過此次數自動 human_handoff

    # clarify 反問的上下文（用戶回答後重新進入哪個決策點）
    "clarify_context": None,  # None | "matching_case" | "deciding_jump" | "questioning"

    # SQL 區塊（_enter_case 時從 how_to_verify 提取，LLM 用 index 引用，不依序執行）
    "sql_blocks": [],           # 當前 case 的所有 SQL template 陣列（來自 SOP 原文）

    # 當前待執行的 SQL（由 LLM 每次指定 index，不自動遞增）
    "current_sql_index": None,  # LLM 指定的 sql_blocks index
    "pending_sql_raw": None,    # sql_blocks[current_sql_index]（SOP 原文，含 &param）
    "pending_sql": None,        # 填入參數後的完整 SQL（等待用戶確認）

    # 狀態機
    "state": "idle",
    # idle | matching_case | questioning | collecting_params
    # | awaiting_sql_confirm | clarifying | done
    # （注意：selecting_case / deciding_jump 已合併進 matching_case）
}
```

### 狀態機流程

```
[idle]
  用戶輸入症狀
      ↓
  router.route() → sop / fallback_chat
      ↓
  [matching_case]
  Vector Search top-3 → LLM 選最符合的 case
      ↓
  LLM 信心度足夠？
  ├── 是 → 直接進入 [questioning]
  └── 否 → [ambiguous_case]
            列出候選讓用戶選擇
            用戶選擇後進入 [questioning]
      ↓
  visited_cases[case_id] >= max_case_visits？
  ├── 是 → human_handoff（防止無限循環）
  └── 否 → visited_cases[case_id] += 1
      ↓
  [questioning]
  problem_to_verify != omit → 向用戶提問
  problem_to_verify == omit → 跳過
      ↓
  [collecting_params]
  LLM 回傳 execute_sql + sql_index
  後端取 sql_blocks[sql_index]（SOP 原文，LLM 不修改 SQL 內容）
  偵測 &param 佔位符，一次性渲染表單讓用戶填入缺失參數
      ↓
  [awaiting_sql_confirm]
  填入參數，輸出完整 SQL，等待 yes / no
      ↓
  yes → 執行 SQL，結果寫入 known_facts
      ↓
  LLM 讀 SQL 結果 + how_to_verify 跳轉條件
  → jump_to_case：跳轉，**保留** collected_params（避免用戶重複輸入相同參數），回到 [questioning]
  → clarify：反問用戶，等待回答後帶新資訊重新進入當前決策點
  → ask_user：收集已知缺少的結構化參數
  → human_handoff：確認超出 SOP 範圍才使用
  → done：流程結束

[done]
  詢問是否有其他問題，若有則 reset 回 [idle]
```

---

## LLM 互動規格

### SOP 模式 System Prompt

```
你是半導體製程疑難雜症排查助手。

規則：
1. 嚴格按照提供的 SOP case 內容執行，不得自行發明步驟或判斷
2. 每次只問一個問題
3. 回覆使用繁體中文
4. 必須以 JSON 格式回覆，不得輸出其他內容
5. 任何決策點若資訊不足以確定下一步，優先使用 clarify 反問用戶，
   不得強行猜測或選擇，也不得過早 human_handoff
```

### Fallback 閒聊模式 System Prompt

```
你是一個友善的助手，使用繁體中文回覆。
```

### LLM 回傳 JSON 格式

```jsonc
// 1. 候選 case 選擇
// 信心度足夠，直接選定
{"chosen_case_id": "case_2", "reason": "用戶提到 foup 未派滿", "confidence": "high"}

// 信心度不足，列出候選讓用戶選
{
  "chosen_case_id": null,
  "confidence": "low",
  "reply_to_user": "找到以下幾個可能相關的情況，請問您遇到的比較像哪一個？\n\n1. **case_1**：XXX Issue 產能下降\n2. **case_5**：設備通訊異常\n3. **case_8**：Container 狀態錯誤"
}

// 2. 向用戶提問
{"next_action": "ask_user", "reply_to_user": "請問 foup exchanger 目前狀態？"}

// 3. 收集 SQL 參數
{
  "next_action": "collect_params",
  "missing_params": ["equipment_id"],
  "reply_to_user": "請提供設備編號："
}

// 4. 等待確認 SQL
{
  "next_action": "ask_sql_confirm",
  "sql_filled": "SELECT count(*) FROM foup_schedule WHERE equipment_id = 'EQ-4721'",
  "reply_to_user": "將執行以下查詢，請確認：\n```sql\n...\n```\n輸入 yes 確認 / no 取消"
}

// 5. 跳轉（告知用戶解讀結果與跳轉目標）
{
  "next_action": "jump_to_case",
  "target_case_id": "case_12",
  "reason": "count=3 > 0",
  "reply_to_user": "查詢結果顯示共有 3 筆 foup 排程（count=3），表示 foup exchanger 已派滿。\n接下來進入 case 12 繼續排查派滿後的情況。"
}

// 6. 補問資訊
{"next_action": "ask_user", "reply_to_user": "請問系統A目前狀態是否正常？"}

// 7. 人工處理（同樣告知結果解讀）
{
  "next_action": "human_handoff",
  "reply_to_user": "查詢結果中 error_code E099 在參考表中查無對應說明。\n此情況超出 SOP 範圍，需通知設備工程師現場確認。"
}
```

---

## 模組規格

### `agent/router.py`

```python
def route(user_input: str, session: dict) -> Literal["sop", "fallback_chat"]:
    """Vector Search + LLM 選 case，只在 state == idle 時呼叫，完成後進入 matching_case"""
    results = vector_search.search_entry_cases(user_input, top_k=3)
    if not results or results[0].score < CONFIDENCE_THRESHOLD:
        session["mode"] = "fallback_chat"
        session["fallback_reason"] = "no_results" if not results else "low_confidence"
        return "fallback_chat"
    # LLM 從候選中選最符合的 case
    chosen = llm_client.select_case(user_input, candidates=results)
    session["mode"] = "sop"
    session["current_sop_file"] = chosen.sop_file
    session["current_case_id"] = chosen.case_id
    return "sop"
```

### `agent/sop_loader.py`

- `load_sop_file(filepath) -> dict`：解析 front matter + 正文
- `get_case(sop_data, case_id) -> str`：取得特定 case 完整 markdown
- `get_case_symptom_summary(sop_data, case_ids) -> list[dict]`：
  取得候選 case 的 `case_id` + `symptom`，供 LLM 候選選擇用
- `extract_sql_blocks(how_to_verify: str) -> list[str]`：
  提取 how_to_verify 裡所有 ```sql``` block，回傳 template 清單
- `extract_sql_placeholders(sql) -> list[str]`
- `fill_sql_params(sql, params) -> str`：將 `&param` 替換為實際值

### `agent/vector_search.py`

- collection：`sop_entry_cases`
- **索引所有 case**（無 `is_entry` 過濾）
- 每個向量的 payload：`{sop_file, case_id, scenario, title, keywords}`
- Embedding 推薦：`BAAI/bge-m3`（中英混合）
- `index_all_sops(sop_dir)`
- `search_entry_cases(query, top_k=3) -> list[SearchResult]`

### `agent/llm_client.py`

- `chat(system, messages, expect_json=True) -> dict`
- `select_case(user_input, candidates) -> SelectedCase`：封裝候選選擇邏輯
- JSON parse 失敗 retry 2 次，仍失敗則 human_handoff

### `agent/sql_executor.py`

- `execute_select(sql) -> list[dict]`
- 驗證 SQL 以 SELECT 開頭，否則拒絕
- 自動加 `LIMIT 200`
- 寫入 audit log

### `agent/param_extractor.py`

- `extract_missing_params(sql, collected) -> list[str]`：偵測 `&param` 格式的佔位符
- `parse_params_from_user_input(user_input, missing) -> dict`

### `agent/session.py`

- `SessionManager`：多用戶 session 管理
- `create_session() -> str`
- `get_session(session_id) -> dict`
- `update_session(session_id, updates)`
- `reset_session(session_id)`：清空狀態，保留 session_id
- `append_known_fact(session_id, fact: str)`：追加 SQL 結果摘要到 known_facts
- `jump_to_case(session_id, new_case_id: str)`：跳轉 case，保留 collected_params，清空 sql_blocks / current_sql_index / pending_sql / pending_sql_raw
- `clear_for_sop_entry(session_id)`：新問題進入時清空所有 SOP 相關狀態（含 visited_cases、collected_params、sql_blocks、current_sql_index、pending_sql、pending_sql_raw）
- `record_case_visit(session_id, case_id) -> bool`：
  記錄訪問次數，若超過 max_case_visits 回傳 False（觸發 human_handoff）

---

## Web API 規格

### API 端點

```
POST   /api/sessions                      → 建立新聊天室
DELETE /api/sessions/{session_id}         → 刪除聊天室
GET    /api/sessions                      → 列出所有聊天室
POST   /api/sessions/{session_id}/chat    → 發送訊息，SSE 串流回覆
```

### SSE 串流事件格式

```jsonc
// 文字片段（顯示在對話氣泡）
data: {"type": "text_delta", "content": "根據 SOP..."}

// SQL 確認卡片
data: {"type": "sql_confirm", "sql": "SELECT ...", "reply": "請確認執行以下 SQL？"}

// 表單收集參數（前端渲染 ParamFormCard，一次性輸入所有缺失參數）
// ⚠️  sse.py 待更新：目前仍使用 ask_user 逐一詢問，需改為發送此事件
data: {
  "type": "collect_params",
  "params": [
    {"name": "equipment_id", "label": "設備編號", "hint": "例如 EQ-4721"},
    {"name": "start_time",   "label": "查詢起始時間", "hint": "例如 2026-03-18 00:00:00"}
  ]
}

// clarify 選項卡片
data: {
  "type": "clarify",
  "reply_to_user": "請問您觀察到的現象比較像哪一種？",
  "options": [
    "設備完全沒有反應",
    "設備有動作但產能下降",
    "設備顯示錯誤代碼"
  ]
}

// 串流結束
data: {"type": "done"}

// 錯誤
data: {"type": "error", "message": "..."}

// ── 透明度事件（更新思考過程區塊）──

// 路由決策
data: {
  "type": "trace_routing",
  "matched_sop": "productivity_lost.md",
  "matched_case": "case_2",
  "case_title": "Scanner Lost + Foup 未派滿",
  "score": 0.88,
  "candidates": [
    {"case_id": "case_1", "score": 0.81, "symptom": "tool scanner lost"},
    {"case_id": "case_2", "score": 0.88, "symptom": "tool scanner lost & foup exchanger 沒派滿"}
  ],
  "selection_reason": "用戶提到 foup 未派滿",
  "mode": "sop"
}

// 進入新 case
data: {
  "type": "trace_case",
  "case_id": "case_2",
  "case_title": "Scanner Lost + Foup 未派滿",
  "scenario": "productivity_lost"
}

// SQL 執行完整記錄
data: {
  "type": "trace_sql",
  "template": "SELECT count(*) FROM foup_schedule WHERE equipment_id = &equipment_id",
  "filled": "SELECT count(*) FROM foup_schedule WHERE equipment_id = 'EQ-4721'",
  "result_rows": 1,
  "result_preview": [{"count": 3}]
}

// 跳轉決策
data: {
  "type": "trace_decision",
  "sql_result_summary": "count=3",
  "condition_matched": "result > 0 → 走 case 12",
  "chosen": "case_12",
  "reason": "count=3 > 0"
}
```

### `api/sse.py` trace 事件發送時機

| 時機 | 事件 |
|------|------|
| `router.route()` 完成 | `trace_routing` |
| 載入新 case | `trace_case` |
| SQL 執行完成 | `trace_sql` |
| LLM 跳轉決策完成 | `trace_decision` |
| LLM 文字回覆逐步生成 | `text_delta` |
| 需要確認 SQL | `sql_confirm` |
| 需要補充參數或提問 | `ask_user` |
| LLM 反問用戶（選項卡片） | `clarify` |
| 完成 | `done` |

---

## Frontend 規格

**技術選型：** Vite + React + TypeScript + Ant Design
- **UI 元件**：Ant Design（`antd`）
- **狀態管理**：Zustand
- **樣式**：Tailwind CSS v4（與 Ant Design 並用，只啟用 utilities layer 避免 preflight 衝突）
- **Markdown 渲染**：`react-markdown` + `react-syntax-highlighter`
- **開發時 proxy**：`vite.config.ts` 設定 `/api` proxy 到 FastAPI

**兩欄佈局：**

```
┌─────────────┬────────────────────────────────────┐
│  聊天室列表  │           對話主區                   │
│             │                                     │
│ + 新增      │  ┌─ 用戶訊息 ──────────────────┐   │
│ ──────────  │  └─────────────────────────────┘   │
│ 聊天室 1 ●  │                                     │
│ 聊天室 2    │  ┌─ Agent 回覆 ────────────────┐   │
│             │  │ 根據 SOP...                  │   │
│             │  └─────────────────────────────┘   │
│             │  ┌─ 🤖 Agent 思考過程 ▼ ────────┐   │
│             │  │  ① 路由決策（候選+選擇理由）  │   │
│             │  │  ② 執行軌跡（case 時間軸）   │   │
│             │  │  ③ SQL 記錄（3段展示）       │   │
│             │  │  ④ 跳轉決策                  │   │
│             │  └─────────────────────────────┘   │
│             │                                     │
│             │  ┌─ 參數輸入表單 ──────────────┐   │
│             │  │ 設備編號    [____________]   │   │
│             │  │ 起始時間    [____________]   │   │
│             │  │           [送出參數]         │   │
│             │  └─────────────────────────────┘   │
│             │                                     │
│             │  ┌─ SQL 確認卡片 ──────────────┐   │
│             │  │      [確認執行]  [取消]       │   │
│             │  └─────────────────────────────┘   │
│             │  ┌─────────────────────────────┐   │
│             │  │ 輸入框              [送出]   │   │
│             │  └─────────────────────────────┘   │
└─────────────┴────────────────────────────────────┘
```

**思考過程區塊（每則 Agent 回覆下方，預設收起）：**

1. **路由決策**：所有候選 case + score 進度條 + LLM 選擇理由
2. **執行軌跡**：case 推進時間軸，當前 case 高亮，已完成打勾
3. **SQL 記錄**：template → 填入參數後 → 查詢結果（表格，超過 10 筆顯示前 10 + 共 N 筆）
4. **跳轉決策**：SQL 結果摘要 + 命中的條件 + 跳轉目標

---

## 設定檔規格（`config.py`）

```python
LLM_BASE_URL = "http://internal-llm:8000/v1"
LLM_API_KEY = "dummy"
LLM_MODEL = "qwen-235b-q8"

# Embedding 模式切換："local" 使用本地模型，"remote" 調用遠端 API
EMBEDDING_MODE = "local"  # "local" | "remote"

# local 模式（EMBEDDING_MODE=local 時使用，remote 模式不下載模型）
EMBEDDING_MODEL = "BAAI/bge-m3"

# remote 模式（EMBEDDING_MODE=remote 時使用）
EMBEDDING_BASE_URL = "http://internal-embedding:8001/v1"
EMBEDDING_API_KEY = "dummy"
EMBEDDING_MODEL_REMOTE = "bge-m3"

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333

DB_DSN = "postgresql://user:password@internal-db:5432/fab_db"

SOP_DIR = "./sop"

CONFIDENCE_THRESHOLD = 0.70
VECTOR_SEARCH_TOP_K = 3       # 候選 case 數量，傳給 LLM 選擇
AUDIT_LOG_FILE = "./logs/audit.log"
```

---

## 錯誤處理規範

| 情境 | 處理方式 |
|------|----------|
| LLM 回傳非 JSON | retry 2 次，仍失敗則 human_handoff |
| 所有候選 score < 閾值 | fallback 閒聊模式 |
| LLM 選擇候選時無法決定 | 列出候選讓用戶自己選 |
| SQL 含非 SELECT | 拒絕執行，記錄 audit log |
| DB 連線失敗 | 回覆「資料庫暫時無法連線」，不 crash |
| 參數提取失敗 | 渲染表單讓用戶一次填入所有缺失參數 |
| jumps_to 的 case_id 不存在 | human_handoff，記錄錯誤 |
| 同一 case 訪問次數超過 max_case_visits | human_handoff，提示可能存在循環跳轉 |
| LLM 任何決策點資訊不足 | clarify 反問用戶，不強行猜測或提早 human_handoff |

---

## Fallback 閒聊模式

```python
if not results or results[0].score < CONFIDENCE_THRESHOLD:
    → 進入閒聊模式
```

- 無話題限制，LLM 自由對話
- 每輪重新做 Vector Search，score 超過閾值自動切回 SOP 模式
- 切回時清空 `collected_params`、`pending_sql`

---

## requirements.txt

```
openai>=1.0.0
qdrant-client>=1.7.0
python-frontmatter>=1.1.0
psycopg2-binary>=2.9.0
fastapi>=0.110.0
uvicorn>=0.27.0
sse-starlette>=1.6.0
sentence-transformers>=2.6.0  # 僅 EMBEDDING_MODE=local 時需要安裝
pydantic>=2.0.0

# frontend（在 frontend/ 目錄下）：
# npm install antd @ant-design/icons zustand react-markdown react-syntax-highlighter
# npm install -D tailwindcss @tailwindcss/vite @types/react-syntax-highlighter
```

---

## 開發順序建議

1. `config.py`
2. `agent/sop_loader.py` — 含新格式的 front matter 解析（cases 陣列）
3. `agent/llm_client.py` — 含 `select_case()` 方法
4. `agent/vector_search.py` — 索引所有 case
5. `agent/router.py` — Vector Search + LLM 候選選擇
6. `agent/param_extractor.py`
7. `agent/sql_executor.py`
8. `agent/session.py` — 含 known_facts 管理，狀態機使用 matching_case
9. `api/routes.py` + `api/sse.py`
10. `frontend/` — Vite 腳手架 + 元件實作
11. `main.py` — 完整端對端測試

---

## 驗收標準

- [ ] 輸入症狀，Vector Search 回傳 top-3 候選
- [ ] LLM 從候選中選出 symptom 最符合的 case
- [ ] symptom 重疊時，條件更具體的 case 優先被選中
- [ ] problem_to_verify != omit 時正確提問；omit 時直接執行
- [ ] SQL 佔位符正確偵測，前端渲染表單一次收集所有缺失參數
- [ ] 填入參數後輸出完整 SQL，等待 yes / no 確認
- [ ] LLM 回傳 sql_index，後端從 sql_blocks 取 SOP 原文 SQL，不經 LLM 生成
- [ ] 只有 yes 後才執行 SQL，非 SELECT 被拒絕
- [ ] LLM 依 how_to_verify 跳轉條件 + SQL 結果正確決定下一個 case
- [ ] jumps_to 只能跳同一份 SOP 內的 case
- [ ] LLM 回傳非 JSON 時不 crash，改為 human_handoff
- [ ] LLM 任何決策點資訊不足時使用 clarify 反問，不強行猜測
- [ ] clarify 顯示為選項卡片，選項按鈕點擊後自動送出
- [ ] clarify 最後一個選項固定為「其他（自由輸入）」，點擊後展開輸入框
- [ ] 選擇後按鈕 disable，不可重複選擇
- [ ] clarify 後用戶回答，系統帶新資訊重新進入同一個決策點
- [ ] clarify 不會觸發 visited_cases 計數（只有真正進入 case 才計數）
- [ ] 同一 case 訪問次數超過 max_case_visits 時自動 human_handoff
- [ ] score 全部低於閾值時進入閒聊模式
- [ ] 閒聊模式不執行 SQL、不載入 SOP
- [ ] 多聊天室並行，session 互不干擾
- [ ] SSE 串流正確，done 事件後連線關閉
- [ ] 思考過程區塊正確顯示路由候選、SQL 記錄、跳轉決策
- [ ] 歷史訊息的思考過程永久保留可查