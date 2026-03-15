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
- **互動方式**：CLI 或 Web API，人工監督

---

## 目錄結構

```
project/
├── spec.md                  # 本文件
├── CLAUDE.md                # Claude Code 行為準則
├── main.py                  # Agent 主程式入口
├── agent/
│   ├── __init__.py
│   ├── session.py           # Session 狀態管理
│   ├── sop_loader.py        # SOP 文件解析
│   ├── vector_search.py     # Qdrant 向量檢索（只索引入口 case）
│   ├── router.py            # 兩階段路由：Vector Search + 條件推進
│   ├── llm_client.py        # LLM API 呼叫
│   ├── sql_executor.py      # SQL 執行（SELECT only）
│   └── param_extractor.py   # SQL 佔位符偵測與參數收集
├── sop/
│   ├── _index.md            # SOP 目錄索引
│   ├── productivity_lost.md # 範例 SOP
│   └── ...                  # 其餘 99 份
├── config.py                # 設定檔（LLM endpoint、DB 連線等）
└── requirements.txt
```

---

## SOP 文件格式規範

每份 SOP 文件為 Markdown，包含 YAML front matter 供機器解析，
以及人類可讀的正文內容。

### Front Matter 規範

```yaml
---
scenario: productivity_lost   # 大分類（底線分隔）
case_id: case_1               # 同 scenario 內唯一編號
title: Tool Scanner Lost      # 簡短標題
is_entry: true                # 關鍵欄位：是否為入口 case（Vector Search 只索引入口）
keywords:                     # 僅 is_entry: true 的 case 需要填，用於 Vector Search
  - scanner lost
  - tool offline
  - productivity lost
jumps_to:                     # action / note 裡可能跳轉的 case_id（限同檔案內）
  - case_2
  - case_3
  - case_4
---
```

**`is_entry` 判斷規則：**
- `true`：symptom 只描述原始報錯或初始症狀，不含任何前置查詢結果作為條件
- `false`：symptom 包含「前面 case 查出來的結論」作為前提條件（如 foup 沒派滿、系統A正常）

### 正文結構規範

```markdown
## case N

### symptom
（症狀描述。入口 case 只寫原始症狀；非入口 case 寫累積條件快照）

### question
（若需要向用戶確認前置資訊則填寫；若不需要則寫 omit）

### action
1. 步驟描述（文字說明或 GUI 操作）
2. 執行以下 SQL：
```sql
SELECT col1, col2
FROM table_name
WHERE equipment_id = '{equipment_id}'
  AND timestamp > '{start_time}'
```
3. 後續步驟...

### note
（跳轉條件與目標 case，例如：「如果 foup 沒派滿，可以考慮 case 2、case 3、case 4」）
```

### SQL 佔位符規範

- 格式統一使用單花括號：`{param_name}`
- param_name 使用底線分隔英文，例如：`{equipment_id}`、`{lot_id}`、`{start_time}`
- 每個佔位符的意義必須能從 action 的上下文推斷，或在 question 中明確詢問

---

## 兩階段路由設計

### 核心設計原則

SOP 的 case 分為兩種類型：

```
入口 case（is_entry: true）
  symptom: tool scanner lost
  → 只描述原始症狀，Vector Search 的搜尋目標

非入口 case（is_entry: false）
  symptom: tool scanner lost
           + foup 沒派滿        ← 來自 case 1 的查詢結果
           + 系統A 正常          ← 又多了一個累積條件
           + mask 在機台內       ← 又多了一個累積條件
  → 累積條件快照，不參與 Vector Search
    由 LLM 條件比對決定是否跳轉至此
```

Vector Search **只索引入口 case**，避免用戶輸入初始症狀時跳進中間節點。

### 階段一：入口匹配（Vector Search）

```
用戶輸入症狀
    ↓
Vector Search（只搜尋 is_entry: true 的 case）
    ↓
score >= CONFIDENCE_THRESHOLD (0.70)？
    ├── 是 → 進入 SOP 模式，載入對應入口 case
    └── 否 → 進入 Fallback 閒聊模式
```

### 階段二：條件推進（State-based，不再用 Vector Search）

```
執行當前 case 的 action（可能含 SQL）
    ↓
SQL 結果 + note 的跳轉提示 → 交給 LLM 做條件比對
    ↓
LLM 判斷：現在已知的狀態，符合 jumps_to 裡哪個 case 的 symptom？
    ↓
跳轉到目標 case，繼續執行
    ↓
直到 note 沒有跳轉（流程結束）或需要人工處理
```

**關鍵：跳轉發生後，LLM 的任務從「自由推理」變成「條件比對」**
——它只需回答「現在的狀態符合哪個 case 的 symptom 描述」，
這是量化弱模型也能可靠完成的任務。

### 條件比對的 LLM Prompt 設計

當 case 執行完畢、note 有跳轉提示時，使用以下 prompt：

```
[已知狀態]
- 原始症狀：tool scanner lost
- case 1 SQL 查詢結果：foup_count = 3（未派滿，規格需 8）
- case 1 note：foup 沒派滿可考慮 case 2、case 3、case 4

[候選 case 的 symptom]
case_2: tool scanner lost + foup 沒派滿 + 系統A異常
case_3: tool scanner lost + foup 沒派滿 + 系統A正常 + mask 不在機台內
case_4: tool scanner lost + foup 沒派滿 + 系統A正常 + mask 在機台內

[任務]
根據已知狀態，判斷最符合哪個 case 的 symptom。
若已知狀態不足以判斷（缺少某個條件的資訊），回傳 ask_user 詢問用戶。
只回傳 JSON，不得輸出其他內容。

輸出格式：
{"next_action": "jump_to_case", "target_case_id": "case_4"}
或
{"next_action": "ask_user", "reply_to_user": "請問系統A目前狀態是否正常？"}
```

---

## Agent 核心邏輯

### Session 狀態結構

```python
session = {
    # 當前位置
    "current_sop_file": "productivity_lost.md",
    "current_case_id": "case_1",

    # 路由模式
    "mode": "sop",            # "sop" | "fallback_chat"
    "fallback_reason": None,  # "no_results" | "low_confidence"

    # 累積的已知狀態（跨 case 保留，用於條件比對）
    "known_facts": [
        "原始症狀：tool scanner lost",
        "case_1 查詢結果：foup_count = 3，未派滿",
    ],

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

    # SQL 暫存（等待用戶確認）
    "pending_sql": None,
    "pending_sql_raw": None,

    # 狀態機
    "state": "idle",  # idle | collecting_params | awaiting_sql_confirm | matching_case | done
}
```

**`known_facts` 的作用：** 跨 case 跳轉時，將前面 case 查詢出的關鍵結論
以自然語言條列累積，每次條件比對都把完整 `known_facts` 傳給 LLM，
確保弱模型不會「忘記」前面查出的結果。

### 狀態機流程

```
[idle]
  用戶輸入症狀
      ↓
  router.route() → 決定 sop / fallback_chat
      ↓
  [sop] 載入入口 case
      ↓
  question != omit？ → 向用戶提問
  question == omit？ → 直接進入 [collecting_params]

[collecting_params]
  偵測 action 中所有 {placeholder}
      ↓
  比對 collected_params，找出缺少的參數
      ↓
  有缺少 → ask_user 逐一詢問
  全部齊全 → 進入 [awaiting_sql_confirm]

[awaiting_sql_confirm]
  填入參數，輸出完整 SQL，等待用戶 yes / no
      ↓
  yes → 執行 SQL → 將結果摘要寫入 known_facts
      → 進入 [matching_case]
  no  → 取消，詢問用戶下一步

[matching_case]
  將 known_facts + jumps_to 候選 case 的 symptom 傳給 LLM
      ↓
  LLM 回傳 jump_to_case → 跳轉，清空 collected_params，回到 [collecting_params]
  LLM 回傳 ask_user     → 向用戶補問缺少的條件，補入 known_facts 後重新比對
  LLM 回傳 human_handoff → 結束，通知人工

[done]
  note 無跳轉，SOP 流程結束
  詢問是否有其他問題，若有則 reset session 重新開始
```

---

## LLM 互動規格

### SOP 模式 System Prompt

```
你是半導體製程疑難雜症排查助手。

規則：
1. 嚴格按照提供的 SOP case 內容執行，不得自行發明步驟或判斷
2. 每次只問一個問題，不要一次問多個
3. 回覆使用繁體中文
4. 必須以 JSON 格式回覆，不得輸出其他內容

輸出格式請見 [current_task] 的說明。
```

### Fallback 閒聊模式 System Prompt

```
你是一個友善的助手，使用繁體中文回覆。
```

### SOP 模式每輪 User Message 結構

```
[當前 SOP Case]
{case 的完整 markdown 內容}

[已知狀態（跨 case 累積）]
{known_facts 條列}

[已收集參數]
{collected_params 的 JSON}

[對話歷史]
{最近 5 輪對話}

[current_task]
{根據當前 state 給出具體指令與 JSON 輸出格式}
```

### LLM 回傳 JSON 格式

```jsonc
// 1. 向用戶提問（question 階段 / 補問缺少條件）
{
  "next_action": "ask_user",
  "reply_to_user": "請問系統A目前狀態是否正常？"
}

// 2. 需要收集 SQL 參數
{
  "next_action": "collect_params",
  "missing_params": ["equipment_id", "start_time"],
  "reply_to_user": "需要執行查詢，請提供：\n• 設備編號 (equipment_id)：\n• 查詢起始時間 (start_time)："
}

// 3. 參數齊全，等待用戶確認 SQL
{
  "next_action": "ask_sql_confirm",
  "sql_filled": "SELECT ... WHERE equipment_id = 'EQ-4721'",
  "reply_to_user": "將執行以下查詢，請確認：\n\n```sql\n...\n```\n\n輸入 yes 確認 / no 取消"
}

// 4. 條件比對後跳轉到同 SOP 的另一個 case
{
  "next_action": "jump_to_case",
  "target_case_id": "case_4",
  "reply_to_user": "根據查詢結果（foup 未派滿、系統A正常、mask 在機台內），進入 case 4 繼續排查..."
}

// 5. 需要人工處理
{
  "next_action": "human_handoff",
  "reply_to_user": "此情況超出 SOP 範圍，請通知製程工程師介入。"
}
```

---

## 模組規格

### `agent/router.py`

每輪用戶輸入的路由決策：

```python
def route(user_input: str, session: dict) -> Literal["sop", "fallback_chat"]:
    results = vector_search.search_entry_cases(user_input, top_k=1)
    if not results or results[0].score < CONFIDENCE_THRESHOLD:
        session["mode"] = "fallback_chat"
        session["fallback_reason"] = "no_results" if not results else "low_confidence"
        return "fallback_chat"
    session["mode"] = "sop"
    session["current_sop_file"] = results[0].sop_file
    session["current_case_id"] = results[0].case_id
    return "sop"
```

注意：`route()` 只在 `state == "idle"` 時呼叫（用戶開始新問題時）。
case 跳轉發生後不重新 route，直接由 LLM 條件比對決定下一個 case。

### `agent/sop_loader.py`

- `load_sop_file(filepath) -> dict`：解析 front matter + 正文
- `get_case(sop_data, case_id) -> str`：取得特定 case 的完整 markdown
- `get_candidate_cases(sop_data, case_ids: list[str]) -> list[dict]`：
  取得多個候選 case 的 `case_id` + `symptom` 摘要，供條件比對用
- `extract_sql_placeholders(sql: str) -> list[str]`：regex 找出所有 `{param}`
- `fill_sql_params(sql: str, params: dict) -> str`：填入參數

### `agent/vector_search.py`

- 使用 Qdrant（本地），collection 名稱：`sop_entry_cases`
- **只索引 `is_entry: true` 的 case**
- Embedding：公司內部 embedding API（OpenAI 相容）或 `sentence-transformers` 本地模型
  推薦模型：`BAAI/bge-m3`（多語言，支援中英混合）
- `index_all_sops(sop_dir)`：啟動時批次建立 index，過濾 `is_entry: false`
- `search_entry_cases(query: str, top_k=1) -> list[SearchResult]`
- payload：`{sop_file, case_id, scenario, title, keywords}`

### `agent/llm_client.py`

- 使用 `openai` 套件，`base_url` 指向內部 LLM
- `chat(system: str, messages: list, expect_json: bool = True) -> dict`
- JSON parse 失敗時最多 retry 2 次，仍失敗則回傳 `{"next_action": "human_handoff", ...}`
- Fallback 閒聊模式呼叫時 `expect_json=False`，直接回傳純文字

### `agent/sql_executor.py`

- `execute_select(sql: str) -> list[dict]`
- **Agent 不自行組合 SQL**，只執行從 SOP template 填入參數後的結果
- SQL 解讀方式（查詢結果代表什麼意義）由 SOP 的 action / note 說明，不由 agent 推斷
- 執行前驗證：`sql.strip().upper().startswith("SELECT")`，否則拒絕
- 自動加上 `LIMIT 200`（若 SQL 未包含 LIMIT）
- 執行結果與 SQL 寫入 audit log

### `agent/param_extractor.py`

- `extract_missing_params(sql: str, collected: dict) -> list[str]`
- `parse_params_from_user_input(user_input: str, missing: list[str]) -> dict`：
  呼叫 LLM 從自然語言輸入提取參數值

### `agent/session.py`

- `SessionManager`：管理多用戶 session（dict，以 session_id 為 key）
- `create_session() -> str`
- `get_session(session_id) -> dict`
- `update_session(session_id, updates: dict)`
- `append_known_fact(session_id, fact: str)`：追加到 known_facts
- `reset_session(session_id)`：保留 session_id，清空所有狀態

---

## 設定檔規格（`config.py`）

```python
LLM_BASE_URL = "http://internal-llm:8000/v1"
LLM_API_KEY = "dummy"
LLM_MODEL = "qwen-235b-q8"

EMBEDDING_BASE_URL = "http://internal-embedding:8001/v1"  # 或 None 使用本地模型
EMBEDDING_MODEL = "BAAI/bge-m3"

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333

DB_DSN = "postgresql://user:password@internal-db:5432/fab_db"

SOP_DIR = "./sop"

# 注意：agent 不自行組合 SQL，只執行 SOP template 填入參數後的結果

CONFIDENCE_THRESHOLD = 0.70  # Vector Search 信心度閾值，上線前用真實問題校準
AUDIT_LOG_FILE = "./audit.log"
```

---

## 錯誤處理規範

| 情境 | 處理方式 |
|------|----------|
| LLM 回傳非 JSON | retry 最多 2 次，仍失敗則 human_handoff |
| Vector Search 找不到入口 case | 進入 fallback 閒聊模式 |
| score 低於閾值 | 進入 fallback 閒聊模式 |
| 條件比對無法決定跳轉目標 | ask_user 補問缺少的條件 |
| SQL 含非 SELECT 語句 | 拒絕執行，記錄 audit log |
| DB 連線失敗 | 回覆「資料庫暫時無法連線」，不 crash |
| 參數提取失敗 | 逐一詢問每個缺少的參數 |

---

## Fallback 閒聊模式

### 觸發條件

```python
if not results or results[0].score < CONFIDENCE_THRESHOLD:
    → 進入閒聊模式
```

### 行為

1. 告知用戶找不到對應 SOP
2. Bypass Agent：完全不使用 JSON 格式，LLM 直接自由對話
3. 無話題限制，正常回覆任何問題
4. 每輪重新做 Vector Search，score 超過閾值自動切回 SOP 模式
5. 切回 SOP 模式時清空 `collected_params`、`pending_sql`、`known_facts`

---

## requirements.txt

```
openai>=1.0.0
qdrant-client>=1.7.0
python-frontmatter>=1.1.0
psycopg2-binary>=2.9.0
fastapi>=0.110.0
uvicorn>=0.27.0
sentence-transformers>=2.6.0
pydantic>=2.0.0
```

---

## 開發順序建議

1. `config.py` — 設定內部 endpoint
2. `agent/sop_loader.py` — 解析 SOP、`get_candidate_cases()`、佔位符處理
3. `agent/llm_client.py` — 確認內部 LLM 可正常呼叫並回傳 JSON
4. `agent/vector_search.py` — 只索引入口 case，測試搜尋準確度
5. `agent/router.py` — 路由邏輯
6. `agent/param_extractor.py` — 佔位符偵測與參數填入
7. `agent/sql_executor.py` — 連接內部 DB，測試 SELECT
8. `agent/session.py` — 含 `known_facts` 管理，組合完整對話流程
9. `main.py` — CLI 入口，完整端對端測試

---

## 驗收標準

- [ ] 輸入初始症狀，Vector Search 能找到正確的入口 case（top-1 準確率 > 85%）
- [ ] `is_entry: false` 的 case 不出現在 Vector Search 結果中
- [ ] question != omit 時正確向用戶提問；question == omit 時直接執行 action
- [ ] SQL 佔位符能被正確偵測，缺少的參數會被逐一詢問
- [ ] 參數齊全後輸出完整 SQL，等待用戶 yes / no 確認
- [ ] 只有用戶輸入 yes 後才執行 SQL
- [ ] 非 SELECT 語句被拒絕執行
- [ ] SQL 結果摘要正確寫入 known_facts
- [ ] 條件比對時 known_facts 完整傳給 LLM，不遺失跨 case 資訊
- [ ] LLM 條件比對能正確跳轉到符合累積條件的非入口 case
- [ ] 條件不足時 ask_user 補問，不擅自猜測跳轉目標
- [ ] LLM 回傳非 JSON 時系統不 crash，改為 human_handoff
- [ ] Vector Search score 低於閾值時進入閒聊模式
- [ ] 閒聊模式下不執行任何 SQL，不載入任何 SOP
- [ ] 閒聊模式下用戶描述新症狀時能重新切回 SOP 模式
