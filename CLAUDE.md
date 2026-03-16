# 智能客服 Agent — Claude Code 行為準則

## 專案說明
這是一個部署於半導體公司內網的智能客服 Agent，
協助工程師查詢製程疑難雜症 SOP 並執行資料庫查詢。
詳細規格見 `spec.md`。

## 開發前必讀
1. 先完整閱讀 `spec.md`，特別注意「路由設計」章節
2. 先瀏覽 `sop/` 目錄，理解新的 SOP 格式：
   - front matter 在文件頂部，包含所有 case 的 metadata（cases 陣列）
   - 欄位名稱：symptom / problem_to_verify / how_to_verify / note
   - 跳轉邏輯寫在 how_to_verify，不在 note

## 程式碼規範

### 語言與風格
- Python 3.11+
- 使用 type hints（所有函數參數與回傳值）
- 使用 `pydantic` 做資料驗證
- 錯誤處理必須明確，不允許裸露的 `except Exception`

### LLM 呼叫
- 統一透過 `agent/llm_client.py` 的 `chat()` 函數
- 禁止在其他模組直接 import openai 並呼叫
- SOP 模式：`expect_json=True`，驗證回傳格式
- Fallback 閒聊模式：`expect_json=False`，回傳純文字
- 候選 case 選擇封裝在 `llm_client.select_case()`

### SQL 安全
- 所有 SQL 執行透過 `agent/sql_executor.py` 的 `execute_select()`
- 禁止在其他模組直接操作資料庫連線
- 禁止 agent 自行組合或生成 SQL，只執行 SOP template 填入參數的結果

### Session 管理
- 所有 session 狀態變更透過 `agent/session.py` 的方法
- 禁止直接修改 session dict
- case 跳轉時清空 `collected_params` 和 `pending_sql`

### Vector Search
- `vector_search.py` 索引**所有** case，無 is_entry 區別
- `router.py` 只在 `state == "idle"` 時呼叫
- case 跳轉後不重新 route，由 LLM 解讀 how_to_verify 決定

## 禁止事項
- 禁止新增任何對外網的 HTTP 請求
- 禁止使用 LangChain、LangGraph 或類似框架
- 禁止在 SQL 執行前跳過用戶確認步驟
- 禁止執行非 SELECT 的 SQL
- 禁止 agent 自行組合或生成 SQL
- 禁止跳轉到 jumps_to 以外的 case
- 禁止跨不同 SOP 檔案跳轉

## 測試要求
每個模組建立對應的單元測試，放在 `tests/` 目錄：
- `test_sop_loader.py`：cases 陣列解析、佔位符偵測、symptom 提取
- `test_sql_executor.py`：非 SELECT 被拒絕、LIMIT 自動加入
- `test_param_extractor.py`：缺少參數正確偵測
- `test_vector_search.py`：所有 case 都在 index 中
- `test_router.py`：score 低於閾值進 fallback、LLM 候選選擇
- `test_session.py`：跳轉後 collected_params 正確清空

## 開發順序
嚴格按照 spec.md 的「開發順序建議」章節，逐步實作並測試，
不要一次生成所有檔案。