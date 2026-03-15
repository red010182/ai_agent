# 智能客服 Agent — Claude Code 行為準則

## 專案說明
這是一個部署於半導體公司內網的智能客服 Agent，
協助工程師查詢製程疑難雜症 SOP 並執行資料庫查詢。
詳細規格見 `spec.md`。

## 開發前必讀
1. 先完整閱讀 `spec.md`，特別注意「兩階段路由設計」章節
2. 先瀏覽 `sop/` 目錄，理解 `is_entry` 欄位的區別與 SQL template 格式

## 程式碼規範

### 語言與風格
- Python 3.11+
- 使用 type hints（所有函數參數與回傳值）
- 使用 `pydantic` 做資料驗證
- 錯誤處理必須明確，不允許裸露的 `except Exception`

### LLM 呼叫
- 統一透過 `agent/llm_client.py` 的 `chat()` 函數
- 禁止在其他模組直接 import openai 並呼叫
- SOP 模式：`expect_json=True`，回傳 JSON 並驗證格式
- Fallback 閒聊模式：`expect_json=False`，回傳純文字，不做 JSON 驗證
- 兩種模式使用不同的 system prompt，見 spec.md「LLM 互動規格」章節

### SQL 安全
- 所有 SQL 執行必須透過 `agent/sql_executor.py` 的 `execute_select()`
- 禁止在其他模組直接操作資料庫連線
- `execute_select()` 內部必須驗證 SQL 以 SELECT 開頭

### Session 管理
- 所有 session 狀態變更透過 `agent/session.py` 的方法
- 禁止直接修改 session dict
- `known_facts` 只能透過 `append_known_fact()` 新增，不可直接賦值
- case 跳轉時清空 `collected_params` 和 `pending_sql`，但保留 `known_facts`

### Vector Search
- `vector_search.py` 只索引 `is_entry: true` 的 case
- 禁止將 `is_entry: false` 的 case 加入 Qdrant index
- `route()` 只在 `state == "idle"` 時呼叫，case 跳轉後不重新 route

## 禁止事項
- 禁止新增任何對外網的 HTTP 請求
- 禁止使用 LangChain、LangGraph 或類似框架
- 禁止在 SQL 執行前跳過用戶確認步驟
- 禁止執行非 SELECT 的 SQL
- 禁止 agent 自行組合或生成 SQL，只能執行 SOP template 填入參數後的結果
- 禁止在 Vector Search index 中加入非入口 case
- 禁止在條件比對時讓 LLM 自由決定跳轉，必須限定在 `jumps_to` 的候選清單內

## 測試要求
每個模組建立對應的單元測試，測試檔案放在 `tests/` 目錄。
核心測試案例：
- `test_sop_loader.py`：佔位符偵測、參數填入、`get_candidate_cases()` 只回傳 symptom 摘要
- `test_sql_executor.py`：非 SELECT 被拒絕、LIMIT 自動加入
- `test_param_extractor.py`：缺少參數正確偵測
- `test_vector_search.py`：確認 `is_entry: false` 的 case 不在 index 中
- `test_router.py`：score 低於閾值進入 fallback、score 高於閾值進入 SOP 模式
- `test_session.py`：`known_facts` 跨 case 跳轉後正確保留

## 開發順序
嚴格按照 spec.md 的「開發順序建議」章節，逐步實作並測試，
不要一次生成所有檔案。
