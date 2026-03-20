"""SSE 串流處理器。

負責將 agent 執行過程轉換為 SSE 事件串流，
並在關鍵時機發送 trace_* 透明度事件。
"""

import asyncio
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import config
from agent import llm_client, vector_search
from agent.param_extractor import parse_params_from_user_input
from agent.session import SessionManager
from agent.sop_loader import (
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

輸出格式請見 [current_task] 的說明。"""

FALLBACK_SYSTEM_PROMPT = "你是一個友善的助手，使用繁體中文回覆。"

_SQL_RE = re.compile(r"```sql\n(.*?)```", re.DOTALL)



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


def _extract_sql_blocks(action: str) -> list[str]:
    return _SQL_RE.findall(action)


def _all_unique_placeholders(sqls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for sql in sqls:
        for p in extract_sql_placeholders(sql):
            if p not in seen:
                seen.add(p)
                result.append(p)
    return result


def _load_case_data(session: dict[str, Any]) -> tuple[dict, dict]:
    sop_data = load_sop_file(
        str(Path(config.SOP_DIR) / session["current_sop_file"])
    )
    case = sop_data["cases"][session["current_case_id"]]
    return sop_data, case


# ── Routing ────────────────────────────────────────────────────────────────────

async def _do_route(
    session_id: str, user_input: str, mgr: SessionManager
) -> tuple[str, list]:
    """向量搜尋路由，回傳 (mode, results)。直接更新 session，不重複搜尋。"""
    results = await asyncio.to_thread(vector_search.search_entry_cases, user_input, 1)
    if not results or results[0].score < config.CONFIDENCE_THRESHOLD:
        mgr.update_session(session_id, {
            "mode": "fallback_chat",
            "fallback_reason": "no_results" if not results else "low_confidence",
        })
        return "fallback_chat", results
    mgr.update_session(session_id, {
        "mode": "sop",
        "fallback_reason": None,
        "current_sop_file": results[0].sop_file,
        "current_case_id": results[0].case_id,
    })
    return "sop", results


# ── State handlers（async generators）─────────────────────────────────────────

async def _enter_case(
    session_id: str, session: dict[str, Any], mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """載入當前 case，發送 trace_case，顯示 question，建立 SQL queue。"""
    sop_data, case = _load_case_data(session)
    metadata = sop_data["metadata"]

    yield _evt(
        "trace_case",
        case_id=session["current_case_id"],
        case_title=metadata.get("title", ""),
        scenario=metadata.get("scenario", ""),
        step="載入 case",
    )

    # 建立 SQL queue
    sql_blocks = _extract_sql_blocks(case["how_to_verify"])
    mgr.update_session(session_id, {
        "state": "collecting_params",
        "sql_queue": sql_blocks,
        "sql_queue_index": 0,
    })

    if not sql_blocks:
        # 無 SQL：顯示 how_to_verify 文字，直接進入條件比對
        yield _evt("text_delta", content=f"請依以下步驟操作：\n\n{case['how_to_verify']}")
        mgr.update_session(session_id, {"state": "matching_case"})
        async for evt in _handle_matching(session_id, mgr.get_session(session_id), mgr):
            yield evt
        return

    # 開場說明：case 名稱 + problem_to_verify
    case_id = session["current_case_id"]
    title = case.get("title", case_id)
    problem = case["problem_to_verify"].strip()
    if problem and problem.lower() != "omit":
        intro = (
            f"這看起來是 **{case_id}：{title}**。\n\n"
            f"為了驗證【{problem}】，需要執行以下查詢："
        )
    else:
        intro = f"這看起來是 **{case_id}：{title}**。\n\n需要執行以下查詢："
    yield _evt("text_delta", content=intro)

    # 立即檢查是否有缺少的參數
    missing = _all_unique_placeholders(sql_blocks)
    missing = [p for p in missing if not session["collected_params"].get(p)]
    if missing:
        # 顯示第一條 SQL 的 template（含佔位符原文）
        first_sql_template = sql_blocks[0]
        yield _evt("text_delta", content=f"\n\n```sql\n{first_sql_template}\n```")
        yield _evt("collect_params", params=missing)
    else:
        async for evt in _show_sql(session_id, mgr.get_session(session_id), mgr):
            yield evt


async def _handle_collecting_params(
    session_id: str, session: dict[str, Any], user_input: str, mgr: SessionManager
) -> AsyncGenerator[dict, None]:
    """從用戶輸入提取參數（支援表單 JSON 直接輸入），齊全時進入 SQL 確認。"""
    sql_blocks = session["sql_queue"]
    collected = session["collected_params"]
    all_params = _all_unique_placeholders(sql_blocks)
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
    """填入下一條 SQL 並以 sql_confirm 事件等待用戶確認。"""
    sql_blocks = session["sql_queue"]
    idx = session["sql_queue_index"]

    if idx >= len(sql_blocks):
        mgr.update_session(session_id, {"state": "matching_case"})
        async for evt in _handle_matching(session_id, mgr.get_session(session_id), mgr):
            yield evt
        return

    sql_raw = sql_blocks[idx]
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
        "pending_sql_raw": sql_raw,
    })
    if idx == 0:
        yield _evt("sql_confirm", sql=sql_filled, reply="")
    else:
        reply = "繼續執行以下查詢，請確認："
        yield _evt("text_delta", content=reply)
        yield _evt("sql_confirm", sql=sql_filled, reply=reply)


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

        mgr.update_session(session_id, {
            "pending_sql": None,
            "pending_sql_raw": None,
            "sql_queue_index": session["sql_queue_index"] + 1,
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
    """LLM 條件比對：決定跳轉目標、繼續下一條 SQL，或向用戶補問缺少的條件。"""
    sop_data, case = _load_case_data(session)
    jumps_to: list[str] = case.get("jumps_to", [])
    sql_queue = session.get("sql_queue", [])
    sql_queue_index = session.get("sql_queue_index", 0)
    has_more_sql = sql_queue_index < len(sql_queue)

    if not jumps_to and not has_more_sql:
        reply = "SOP 流程完成，問題排查結束。如有其他問題請重新描述症狀。"
        yield _evt("text_delta", content=reply)
        mgr.update_session(session_id, {"state": "done"})
        return

    candidates = get_case_symptom_summary(sop_data, jumps_to) if jumps_to else []
    known_facts_text = "\n".join(f"- {f}" for f in session["known_facts"])
    candidates_text = "\n".join(f"{c['case_id']}: {c['symptom']}" for c in candidates)
    how_to_verify = case.get("how_to_verify", "")

    continue_sql_hint = (
        f"目前尚有 {len(sql_queue) - sql_queue_index} 條 SQL 待執行（continue_sql 可繼續）。\n"
    ) if has_more_sql else ""

    # "reply_to_user 必須包含：\n"
    #     "1. SQL 查詢結果的解讀方式（數值代表什麼、判斷依據）\n"
    #     "2. 根據 how_to_verify 哪條規則選擇該動作\n\n"
    #     "reply_to_user 必須使用 Markdown 格式：\n"
    #     "- **粗體** 標示關鍵數值或 case 名稱\n"
    #     "- 條列式 `-` 列出多個判斷依據\n"
    #     "- `code` 標示 SQL 欄位名或數值\n\n"

    prompt = (
        f"[當前 case 的 how_to_verify]\n{how_to_verify}\n\n"
        f"[SQL 執行結果（已知狀態）]\n{known_facts_text}\n\n"
        f"[候選 case 的 symptom]\n{candidates_text}\n\n"
        "[判斷規則（依序執行，命中即停止）]\n"
        "1. 先檢查 how_to_verify 中所有跳轉條件：若當前 SQL 結果已滿足任一條件，"
        "立即輸出 jump_to_case，不得繼續執行後續 SQL。\n"
        f"2. 若所有跳轉條件均不滿足：{continue_sql_hint}"
        "   - 有剩餘 SQL 且 how_to_verify 要求繼續後續步驟 → 輸出 continue_sql\n"
        "   - 無剩餘 SQL 且條件仍不明確 → 輸出 ask_user\n"
        "只回傳 JSON，不得輸出其他內容。\n\n"
        "reply_to_user 規則（必填，不得為空）：\n"
        "- 第一句：用一句話解讀當前 SQL 結果的意義（數值代表什麼現象）\n"
        "- jump_to_case：第二句說明即將進入哪個 case，以 **粗體** 標示 case 名稱\n"
        "- continue_sql：第二句說明結果符合 how_to_verify 哪個繼續條件，因此執行下一步\n"
        "- 禁止輸出內部推理、引用 how_to_verify 條文原文或 case_id 編號\n\n"
        "輸出格式：\n"
        + ('{"next_action": "continue_sql", "reply_to_user": "..."}\n或\n' if has_more_sql else "")
        + '{"next_action": "jump_to_case", "target_case_id": "case_X", "reply_to_user": "..."}\n'
        "或\n"
        '{"next_action": "ask_user", "reply_to_user": "..."}\n'
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

    # trace_decision
    yield _evt(
        "trace_decision",
        candidates=candidates,
        chosen=result.get("target_case_id") if action == "jump_to_case" else None,
        reason=reply,
    )

    if reply is not None:
        yield _evt("text_delta", content=reply)

    if action == "continue_sql":
        mgr.update_session(session_id, {"state": "collecting_params"})
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

    elif action == "ask_user":
        mgr.update_session(session_id, {"state": "matching_case"})
        if reply:
            yield _evt("ask_user", reply=reply)

    elif action == "human_handoff":
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
            mode, results = await _do_route(session_id, user_input, mgr)

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
            mode, results = await _do_route(session_id, user_input, mgr)

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

    async for evt in _agent_turn_impl(session_id, user_input):
        data = json.loads(evt["data"])
        t = data["type"]
        if t == "text_delta":
            print(f"  [text_delta] {data.get('content', '')}")
        elif t == "sql_confirm":
            print(f"  [sql_confirm] {data.get('sql', '')}")
        elif t == "ask_user":
            print(f"  [ask_user]   {data.get('reply', '')}")
        elif t == "error":
            print(f"  [error]      {data.get('message', '')}")
        elif t == "done":
            print(f"  [done]")
        else:
            print(f"  [{t}]")
        yield evt
