"""SSE 串流處理器。

負責將 agent 執行過程轉換為 SSE 事件串流，
並在關鍵時機發送 trace_* 透明度事件。
"""

import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import config
from agent import llm_client, vector_search
from agent.param_extractor import parse_params_from_user_input
from agent.session import SessionManager
from agent.sop_loader import (
    extract_sql_blocks,
    extract_sql_placeholders,
    fill_sql_params,
    get_case_symptom_summary,
    load_sop_file,
)
from agent.sql_executor import DBConnectionError, SQLExecutionError, SQLRejectedError, execute_select

# ── Prompts ────────────────────────────────────────────────────────────────────

SOP_SYSTEM_PROMPT = """\
你是半導體製程疑難雜症排查助手。

規則：
1. 嚴格按照提供的 SOP case 內容執行，不得自行發明步驟或判斷
2. 每次只問一個問題，不要一次問多個
3. 回覆使用繁體中文
4. 必須以 JSON 格式回覆，不得輸出其他內容
5. 任何決策點若資訊不足，優先使用 clarify 反問，不得強行猜測，也不得過早 human_handoff

輸出格式請見 [current_task] 的說明。"""

FALLBACK_SYSTEM_PROMPT = "你是一個友善的助手，使用繁體中文回覆。"


def _try_parse_form_input(user_input: str) -> dict[str, str] | None:
    """若 user_input 是表單送出的 JSON dict（字串值），直接回傳；否則回傳 None。"""
    stripped = user_input.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()):
            return data
    except json.JSONDecodeError:
        pass
    return None


# ── SessionRegistry ────────────────────────────────────────────────────────────

class SessionRegistry:
    """封裝 SessionManager，為 API 層補充 created_at 等 metadata。"""

    def __init__(self) -> None:
        self._mgr = SessionManager()
        self._meta: dict[str, dict[str, Any]] = {}

    def create(self) -> str:
        session_id = self._mgr.create_session()
        self._meta[session_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return session_id

    def exists(self, session_id: str) -> bool:
        return session_id in self._meta

    def delete(self, session_id: str) -> None:
        # SessionManager 無 delete 方法；session 資料留在記憶體（v1 可接受）
        self._meta.pop(session_id, None)

    def get_meta(self, session_id: str) -> dict[str, Any]:
        return self._meta[session_id]

    def list_all(self) -> list[dict[str, Any]]:
        result = []
        for sid, meta in self._meta.items():
            try:
                session = self._mgr.get_session(sid)
                result.append({
                    "session_id": sid,
                    "created_at": meta["created_at"],
                    "mode": session.get("mode", "idle"),
                })
            except KeyError:
                pass
        return result

    @property
    def mgr(self) -> SessionManager:
        return self._mgr


# 全域單例，供 routes.py 引用
agent_registry = SessionRegistry()


# ── SSE event helpers ──────────────────────────────────────────────────────────

def _evt(type_: str, **kwargs: Any) -> dict[str, str]:
    """建立 sse-starlette 格式的事件 dict。"""
    return {"data": json.dumps({"type": type_, **kwargs}, ensure_ascii=False, default=str)}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _rows_to_markdown_table(rows: list[dict], max_rows: int = 10) -> str:
    """將 DB 結果列表轉成 Markdown table（最多顯示 max_rows 筆）。"""
    if not rows:
        return ""
    headers = list(rows[0].keys())
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    header_row = "| " + " | ".join(headers) + " |"
    data_rows = [
        "| " + " | ".join(str(row.get(h, "")) for h in headers) + " |"
        for row in rows[:max_rows]
    ]
    return "\n".join([header_row, sep] + data_rows)


def _load_case_data(session: dict[str, Any]) -> tuple[dict, dict]:
    sop_data = load_sop_file(
        str(Path(config.SOP_DIR) / session["current_sop_file"])
    )
    case = sop_data["cases"][session["current_case_id"]]
    return sop_data, case


# ── Routing ────────────────────────────────────────────────────────────────────

async def _do_route(
    session_id: str, user_input: str, mgr: SessionManager
) -> tuple[str, list, dict]:
    """向量搜尋路由，回傳 (mode, results, extra)。直接更新 session，不重複搜尋。

    extra 在 mode=="ambiguous_case" 時包含 {"candidates": [...], "reply": "..."}。
    """
    results = await asyncio.to_thread(vector_search.search_entry_cases, user_input, 3)
    above = [r for r in results if r.score >= config.CONFIDENCE_THRESHOLD]

    if not above:
        mgr.update_session(session_id, {
            "mode": "fallback_chat",
            "fallback_reason": "no_results" if not results else "low_confidence",
        })
        return "fallback_chat", results, {}

    if len(above) == 1:
        r = above[0]
        mgr.update_session(session_id, {
            "mode": "sop",
            "fallback_reason": None,
            "current_sop_file": r.sop_file,
            "current_case_id": r.case_id,
        })
        return "sop", above, {}

    # 多個候選：載入各 case 的 symptom，請 LLM 選擇
    candidates: list[dict] = []
    for r in above:
        sop_data = load_sop_file(str(Path(config.SOP_DIR) / r.sop_file))
        case = sop_data["cases"][r.case_id]
        candidates.append({
            "case_id": r.case_id,
            "title": r.title,
            "symptom": case["symptom"],
            "sop_file": r.sop_file,
        })

    selection: dict = await asyncio.to_thread(llm_client.select_case, user_input, candidates)

    if selection.get("confidence") == "high":
        chosen_id = selection.get("chosen_case_id")
        chosen = next((c for c in candidates if c["case_id"] == chosen_id), candidates[0])
        mgr.update_session(session_id, {
            "mode": "sop",
            "fallback_reason": None,
            "current_sop_file": chosen["sop_file"],
            "current_case_id": chosen["case_id"],
        })
        return "sop", above, {}

    # confidence == "low"：讓用戶選擇
    reply = selection.get("reply_to_user", "找到以下幾個可能符合的情況，請選擇最符合的：")
    mgr.update_session(session_id, {
        "mode": "sop",
        "fallback_reason": None,
        "state": "ambiguous_case",
        "ambiguous_case_candidates": candidates,
    })
    return "ambiguous_case", above, {"candidates": candidates, "reply": reply}


# ── State handlers（async generators）─────────────────────────────────────────

async def _enter_case(
    session_id: str, session: dict[str, Any], mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """載入當前 case，發送 trace_case，顯示開場介紹，交由 LLM 決定第一步。"""
    case_id = session["current_case_id"]

    # 迴圈偵測：同一 case 進入次數超過上限 → human_handoff
    if not mgr.record_case_visit(session_id, case_id):
        msg = f"⚠️ 偵測到重複進入 **{case_id}**，可能發生無限迴圈。請通知工程師協助處理。"
        yield _evt("text_delta", content=msg)
        mgr.update_session(session_id, {"state": "done"})
        return

    sop_data, case = _load_case_data(session)
    metadata = sop_data["metadata"]

    yield _evt(
        "trace_case",
        case_id=case_id,
        case_title=metadata.get("title", ""),
        scenario=metadata.get("scenario", ""),
        step="載入 case",
    )

    # 提取 how_to_verify 中所有 SQL block，存入 session
    sql_blocks = extract_sql_blocks(case.get("how_to_verify", ""))
    mgr.update_session(session_id, {"sql_blocks": sql_blocks, "current_sql_index": None})

    # 開場說明：case 名稱 + problem_to_verify
    title = case.get("title", case_id)
    problem = case.get("problem_to_verify", "").strip()
    if problem and problem.lower() != "omit":
        intro = (
            f"這看起來是 **{case_id}：{title}**。\n\n"
            f"為了驗證【{problem}】，開始執行排查流程。"
        )
    else:
        intro = f"這看起來是 **{case_id}：{title}**。\n\n開始執行排查流程。"
    yield _evt("text_delta", content=intro)

    mgr.update_session(session_id, {"state": "matching_case"})
    async for evt in _handle_matching(session_id, mgr.get_session(session_id), mgr):
        yield evt


async def _handle_collecting_params(
    session_id: str, session: dict[str, Any], user_input: str, mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """從用戶輸入提取參數（支援表單 JSON 直接輸入），齊全時進入 SQL 確認。"""
    sql_raw = session.get("pending_sql_raw") or ""
    collected = session["collected_params"]
    all_params = extract_sql_placeholders(sql_raw)
    missing = [p for p in all_params if not collected.get(p)]

    if user_input and missing:
        # 優先嘗試解析表單送出的 JSON（直接賦值，不過 LLM）
        form_data = _try_parse_form_input(user_input)
        if form_data:
            extracted = {k: v for k, v in form_data.items() if v}
        else:
            extracted = await asyncio.to_thread(
                parse_params_from_user_input, user_input, missing
            )
        new_collected = {**collected, **{k: v for k, v in extracted.items() if v}}
        mgr.update_session(session_id, {"collected_params": new_collected})
        missing = [p for p in all_params if not new_collected.get(p)]

    if missing:
        yield _evt("collect_params", params=missing)
    else:
        async for evt in _show_sql(session_id, mgr.get_session(session_id), mgr):
            yield evt


async def _show_sql(
    session_id: str, session: dict[str, Any], mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """將 pending_sql_raw 填入參數，以 sql_confirm 事件等待用戶確認。"""
    sql_raw = session.get("pending_sql_raw") or ""
    try:
        sql_filled = fill_sql_params(sql_raw, session["collected_params"])
    except KeyError as e:
        reply = f"參數缺失 {e}，請重新提供。"
        yield _evt("text_delta", content=reply)
        yield _evt("ask_user", reply=reply)
        mgr.update_session(session_id, {"state": "collecting_params"})
        return

    mgr.update_session(session_id, {
        "state": "awaiting_sql_confirm",
        "pending_sql": sql_filled,
    })
    yield _evt("sql_confirm", sql=sql_filled, reply="")


async def _handle_sql_confirm(
    session_id: str, session: dict[str, Any], user_input: str, mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """處理用戶對 SQL 的 yes / no 確認。"""
    ans = user_input.strip().lower()

    if ans == "yes":
        sql = session["pending_sql"]
        sql_raw = session["pending_sql_raw"]

        try:
            rows = await asyncio.to_thread(execute_select, sql)
        except DBConnectionError as e:
            yield _evt("text_delta", content=str(e))
            yield _evt("error", message=str(e))
            mgr.update_session(session_id, {"state": "idle"})
            return
        except SQLRejectedError as e:
            yield _evt("text_delta", content=f"SQL 被拒絕：{e}")
            yield _evt("error", message=str(e))
            mgr.update_session(session_id, {"state": "idle"})
            return
        except SQLExecutionError as e:
            yield _evt(
                "sql_error",
                error_message=e.error_message,
                sql=e.sql,
                hint="請確認 SOP 中的 SQL 語法是否正確，例如欄位名稱、資料表名稱是否有誤。",
            )
            mgr.update_session(session_id, {"state": "idle"})
            return

        preview = rows[:10]
        count_text = f"查詢回傳 {len(rows)} 筆"
        table_md = _rows_to_markdown_table(rows, max_rows=10)
        display = count_text if not rows else f"{count_text}\n\n{table_md}"

        # trace_sql：完整 SQL 執行過程
        yield _evt(
            "trace_sql",
            template=sql_raw,
            filled=sql,
            result_rows=len(rows),
            result_preview=preview,
        )
        yield _evt("text_delta", content=display)

        # 寫入 known_facts → trace_facts（用精簡的 count_text，不含 table）
        mgr.append_known_fact(
            session_id,
            f"{session['current_case_id']} SQL 查詢結果：{count_text}，"
            f"前 3 筆：{json.dumps(rows[:3], ensure_ascii=False, default=str)}",
        )
        yield _evt(
            "trace_facts",
            known_facts=mgr.get_session(session_id)["known_facts"],
        )

        # 記錄已執行的 sql_index，防止 LLM 重複執行同一條 SQL
        executed = list(session.get("executed_sql_indexes", []))
        if session.get("current_sql_index") is not None:
            executed.append(session["current_sql_index"])
        mgr.update_session(session_id, {
            "pending_sql": None,
            "pending_sql_raw": None,
            "executed_sql_indexes": executed,
            "state": "matching_case",
        })
        async for evt in _handle_matching(session_id, mgr.get_session(session_id), mgr):
            yield evt

    elif ans == "no":
        reply = "已取消 SQL 執行。請問您想如何繼續？"
        yield _evt("text_delta", content=reply)
        mgr.update_session(session_id, {
            "state": "idle",
            "pending_sql": None,
            "pending_sql_raw": None,
        })

    else:
        reply = "請輸入 yes 確認執行，或 no 取消。"
        yield _evt("text_delta", content=reply)
        yield _evt("sql_confirm", sql=session["pending_sql"], reply=reply)


async def _handle_matching(
    session_id: str, session: dict[str, Any], mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """LLM 決策點：決定執行哪條 SQL、跳轉目標，或反問用戶。"""
    sop_data, case = _load_case_data(session)
    jumps_to: list[str] = case.get("jumps_to", [])
    how_to_verify = case.get("how_to_verify", "")

    candidates = get_case_symptom_summary(sop_data, jumps_to) if jumps_to else []
    known_facts_text = "\n".join(f"- {f}" for f in session["known_facts"]) or "（尚無）"
    candidates_text = "\n".join(f"{c['case_id']}: {c['symptom']}" for c in candidates)

    sql_blocks = session.get("sql_blocks", [])
    executed_indexes: set[int] = set(session.get("executed_sql_indexes", []))
    sql_list_text = "\n".join(
        f"[{i}] {'[已執行]' if i in executed_indexes else '[未執行]'} {s}"
        for i, s in enumerate(sql_blocks)
    )

    prompt = (
        f"[當前 case 的 how_to_verify]\n{how_to_verify}\n\n"
        + (f"[可用 SQL 清單（依索引引用）]\n{sql_list_text}\n\n" if sql_blocks else "")
        + f"[SQL 執行結果（已知狀態）]\n{known_facts_text}\n\n"
        + (f"[候選 case 的 symptom]\n{candidates_text}\n\n" if candidates else "")
        + "[判斷規則（依序執行，命中即停止）]\n"
        "1. 先檢查 how_to_verify 中所有跳轉條件：若已知狀態已滿足任一條件 → jump_to_case。\n"
        "2. 若 how_to_verify 要求執行 SQL 查詢，且該 sql_index 標記為 [未執行]"
        " → execute_sql，指定可用 SQL 清單中的 sql_index，不得自行撰寫或修改 SQL，不得重複執行 [已執行] 的 SQL。\n"
        "3. 若資訊不足以判斷 → clarify。\n"
        "4. 若所有 SQL 均已執行且無滿足跳轉條件 → done。\n"
        "5. 已充分反問後仍無法判斷 → human_handoff。\n"
        "只回傳 JSON，不得輸出其他內容。\n\n"
        "reply_to_user 規則（必填，不得為空）：\n"
        "- execute_sql：說明即將執行的查詢步驟及目的\n"
        "- jump_to_case：說明即將進入哪個 case，以 **粗體** 標示 case 名稱\n"
        "- done：簡短說明排查結論\n"
        "- clarify：提出明確反問；options 提供 2~4 個選項，每項不超過 20 字\n"
        "- 禁止輸出內部推理、引用 how_to_verify 條文原文或 case_id 編號\n\n"
        "輸出格式：\n"
        '{"next_action": "execute_sql", "sql_index": 0, "reply_to_user": "..."}\n'
        "或\n"
        '{"next_action": "jump_to_case", "target_case_id": "case_X", "reply_to_user": "..."}\n'
        "或\n"
        '{"next_action": "clarify", "reply_to_user": "...", "options": ["選項1", "選項2"]}\n'
        "或\n"
        '{"next_action": "done", "reply_to_user": "..."}\n'
        "或\n"
        '{"next_action": "human_handoff", "reply_to_user": "..."}'
    )

    result: dict = await asyncio.to_thread(
        llm_client.chat,
        SOP_SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        True,
    )

    action = result.get("next_action", "human_handoff")
    reply = result.get("reply_to_user", "")
    valid_ids = [c["case_id"] for c in candidates]

    yield _evt(
        "trace_decision",
        candidates=candidates,
        chosen=result.get("target_case_id") if action == "jump_to_case" else None,
        reason=reply,
    )

    if reply is not None and action != "clarify":
        yield _evt("text_delta", content="\n\n" + reply)

    if action == "execute_sql":
        sql_index = result.get("sql_index")
        if sql_index is None or not isinstance(sql_index, int) or sql_index >= len(sql_blocks):
            msg = f"[系統] 無效的 sql_index（{sql_index}），請通知工程師。"
            yield _evt("text_delta", content=msg)
            mgr.update_session(session_id, {"state": "done"})
            return
        sql_raw = sql_blocks[sql_index]
        mgr.update_session(session_id, {
            "pending_sql_raw": sql_raw,
            "current_sql_index": sql_index,
            "state": "collecting_params",
        })
        session = mgr.get_session(session_id)
        missing = extract_sql_placeholders(sql_raw)
        missing = [p for p in missing if not session["collected_params"].get(p)]
        if missing:
            yield _evt("text_delta", content=f"\n\n```sql\n{sql_raw}\n```")
            yield _evt("collect_params", params=missing)
        else:
            async for evt in _show_sql(session_id, mgr.get_session(session_id), mgr):
                yield evt

    elif action == "jump_to_case":
        target = result.get("target_case_id", "")
        if target in valid_ids:
            target_title = sop_data["cases"][target].get("title", target)
            yield _evt("text_delta", content=f"\n→ 進入 {target}：{target_title}")
            mgr.jump_to_case(session_id, target)
            async for evt in _enter_case(session_id, mgr.get_session(session_id), mgr):
                yield evt
        else:
            msg = f"[系統] 無效跳轉目標 '{target}'，請通知工程師。"
            yield _evt("text_delta", content=msg)
            mgr.update_session(session_id, {"state": "done"})

    elif action == "clarify":
        mgr.update_session(session_id, {
            "state": "clarifying",
            "clarify_context": "matching_case",
        })
        yield _evt("clarify", reply=reply, options=result.get("options", []))

    elif action in ("done", "human_handoff"):
        mgr.update_session(session_id, {"state": "done"})

    else:
        msg = "[系統] AI 回應格式異常，請通知工程師。"
        yield _evt("text_delta", content=msg)
        mgr.update_session(session_id, {"state": "done"})


async def _handle_fallback(
    session_id: str, session: dict[str, Any], user_input: str, mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """Fallback 閒聊：LLM streaming 逐 chunk 輸出。"""
    history = session["conversation_history"][-10:]
    messages = history + [{"role": "user", "content": user_input}]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _produce() -> None:
        try:
            for chunk in llm_client.chat_stream(FALLBACK_SYSTEM_PROMPT, messages):
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, f"\n\n⚠️ 串流錯誤：{e}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_produce, daemon=True).start()

    full_reply = ""
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        full_reply += chunk
        yield _evt("text_delta", content=chunk)

    # 更新對話歷史（conversation_history 無專屬 SessionManager 方法，直接 append）
    session["conversation_history"].append({"role": "user", "content": user_input})
    session["conversation_history"].append({"role": "assistant", "content": full_reply})


# ── Main dispatcher ────────────────────────────────────────────────────────────

async def _agent_turn_impl(
    session_id: str, user_input: str
) -> AsyncGenerator[dict, None]:
    """對一輪用戶輸入執行 agent 流程，逐一 yield SSE 事件。"""
    mgr = agent_registry.mgr
    session = mgr.get_session(session_id)
    state = session["state"]

    try:
        # ── Fallback 模式：每輪重新 route ──────────────────────────────────────
        if session["mode"] == "fallback_chat" and state == "idle":
            mode, results, extra = await _do_route(session_id, user_input, mgr)

            if mode == "sop":
                mgr.clear_for_sop_entry(session_id)
                session = mgr.get_session(session_id)
                r = results[0]
                sop_data = load_sop_file(str(Path(config.SOP_DIR) / r.sop_file))
                yield _evt(
                    "trace_routing",
                    matched_sop=r.sop_file,
                    matched_case=r.case_id,
                    case_title=sop_data["metadata"].get("title", ""),
                    score=round(r.score, 4),
                    mode="sop",
                )
                yield _evt("text_delta", content="已找到對應 SOP，開始排查流程。")
                mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
                yield _evt("trace_facts", known_facts=mgr.get_session(session_id)["known_facts"])
                async for evt in _enter_case(session_id, mgr.get_session(session_id), mgr):
                    yield evt
            elif mode == "ambiguous_case":
                yield _evt(
                    "trace_routing",
                    matched_sop=None, matched_case=None,
                    case_title=None, score=round(results[0].score, 4), mode="sop",
                )
                mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
                yield _evt("trace_facts", known_facts=mgr.get_session(session_id)["known_facts"])
                yield _evt(
                    "select_case",
                    candidates=extra["candidates"],
                    reply=extra["reply"],
                )
            else:
                score = round(results[0].score, 4) if results else 0.0
                yield _evt(
                    "trace_routing",
                    matched_sop=None, matched_case=None,
                    case_title=None, score=score, mode="fallback_chat",
                )
                async for evt in _handle_fallback(session_id, session, user_input, mgr):
                    yield evt

        # ── Idle：首次輸入症狀 ──────────────────────────────────────────────────
        elif state == "idle":
            mode, results, extra = await _do_route(session_id, user_input, mgr)

            if mode == "sop":
                r = results[0]
                sop_data = load_sop_file(str(Path(config.SOP_DIR) / r.sop_file))
                yield _evt(
                    "trace_routing",
                    matched_sop=r.sop_file,
                    matched_case=r.case_id,
                    case_title=sop_data["metadata"].get("title", ""),
                    score=round(r.score, 4),
                    mode="sop",
                )
                mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
                yield _evt("trace_facts", known_facts=mgr.get_session(session_id)["known_facts"])
                async for evt in _enter_case(session_id, mgr.get_session(session_id), mgr):
                    yield evt
            elif mode == "ambiguous_case":
                yield _evt(
                    "trace_routing",
                    matched_sop=None, matched_case=None,
                    case_title=None, score=round(results[0].score, 4), mode="sop",
                )
                mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
                yield _evt("trace_facts", known_facts=mgr.get_session(session_id)["known_facts"])
                yield _evt(
                    "select_case",
                    candidates=extra["candidates"],
                    reply=extra["reply"],
                )
            else:
                score = round(results[0].score, 4) if results else 0.0
                yield _evt(
                    "trace_routing",
                    matched_sop=None, matched_case=None,
                    case_title=None, score=score, mode="fallback_chat",
                )
                yield _evt("text_delta", content="目前找不到對應的 SOP，我會盡力協助您。")
                async for evt in _handle_fallback(session_id, session, user_input, mgr):
                    yield evt

        # ── 用戶選擇 case（ambiguous_case 狀態）──────────────────────────────────
        elif state == "ambiguous_case":
            candidates = session.get("ambiguous_case_candidates", [])
            chosen = next((c for c in candidates if c["case_id"] == user_input.strip()), None)
            if chosen:
                mgr.update_session(session_id, {
                    "state": "idle",
                    "ambiguous_case_candidates": [],
                    "current_sop_file": chosen["sop_file"],
                    "current_case_id": chosen["case_id"],
                })
                mgr.append_known_fact(session_id, f"用戶選擇 case：{chosen['case_id']}")
                yield _evt("trace_facts", known_facts=mgr.get_session(session_id)["known_facts"])
                async for evt in _enter_case(session_id, mgr.get_session(session_id), mgr):
                    yield evt
            else:
                # 無效選擇：重新發送候選清單
                yield _evt(
                    "select_case",
                    candidates=candidates,
                    reply="請選擇其中一個選項：",
                )

        # ── Clarify：用戶回答反問後，帶新資訊重新進入原決策點 ──────────────────
        elif state == "clarifying":
            context = session.get("clarify_context")
            mgr.append_known_fact(session_id, f"用戶補充：{user_input}")
            yield _evt("trace_facts", known_facts=mgr.get_session(session_id)["known_facts"])
            mgr.update_session(session_id, {"clarify_context": None})
            if context == "matching_case":
                mgr.update_session(session_id, {"state": "matching_case"})
                async for evt in _handle_matching(session_id, mgr.get_session(session_id), mgr):
                    yield evt
            else:
                # 未知 context，回到 idle 重新路由
                mgr.update_session(session_id, {"state": "idle"})
                async for evt in _agent_turn_impl(session_id, user_input):
                    yield evt
                return

        # ── 參數收集 ────────────────────────────────────────────────────────────
        elif state == "collecting_params":
            async for evt in _handle_collecting_params(
                session_id, session, user_input, mgr
            ):
                yield evt

        # ── SQL 確認 ────────────────────────────────────────────────────────────
        elif state == "awaiting_sql_confirm":
            async for evt in _handle_sql_confirm(
                session_id, session, user_input, mgr
            ):
                yield evt

        # ── 條件比對（用戶補充條件）────────────────────────────────────────────
        elif state == "matching_case":
            mgr.append_known_fact(session_id, f"用戶補充：{user_input}")
            yield _evt(
                "trace_facts",
                known_facts=mgr.get_session(session_id)["known_facts"],
            )
            async for evt in _handle_matching(
                session_id, mgr.get_session(session_id), mgr
            ):
                yield evt

        # ── Done：重新開始 ──────────────────────────────────────────────────────
        elif state == "done":
            mgr.reset_session(session_id)
            yield _evt("text_delta", content="開始新一輪問題排查。")
            async for evt in _agent_turn_impl(session_id, user_input):
                yield evt
            return  # 內層已 yield done，跳過外層的 done

        yield _evt("done")

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield _evt("error", message=str(e))
        yield _evt("done")


async def run_agent_turn(
    session_id: str, user_input: str
) -> AsyncGenerator[dict, None]:
    """公開入口：印出對話內容後委派給 _agent_turn_impl。"""
    sid = session_id[:8]
    print(f"\n{'='*60}")
    print(f"[{sid}] USER: {user_input}")
    print(f"{'='*60}")

    text_buf = ""
    async for evt in _agent_turn_impl(session_id, user_input):
        data = json.loads(evt["data"])
        t = data["type"]
        if t == "text_delta":
            text_buf += data.get("content", "")
        else:
            if text_buf:
                print(f"  [text_delta] {text_buf}")
                text_buf = ""
            if t == "sql_confirm":
                print(f"  [sql_confirm] {data.get('sql', '')}")
            elif t == "ask_user":
                print(f"  [ask_user]   {data.get('reply', '')}")
            elif t == "error":
                print(f"  [error]      {data.get('message', '')}")
            elif t == "done":
                print(f"  [done]")
            elif t == "collect_params":
                print(f"  [collect_params] {data.get('params', [])}")
            elif t == "trace_routing":
                print(f"  [trace_routing] mode={data.get('mode')} score={data.get('score')}")
            else:
                print(f"  [{t}]")
        yield evt
