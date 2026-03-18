#!/usr/bin/env python3
"""智能客服 Agent — CLI 入口（單用戶終端模式）。

執行：
    python cli.py
    或
    .venv/bin/python cli.py
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import config
from agent import llm_client, vector_search
from agent.param_extractor import extract_missing_params, parse_params_from_user_input
from agent.router import route
from agent.session import SessionManager
from agent.sop_loader import (
    extract_sql_placeholders,
    fill_sql_params,
    get_case_symptom_summary,
    load_sop_file,
)
from agent.sql_executor import DBConnectionError, SQLRejectedError, execute_select

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

# ── Helpers ────────────────────────────────────────────────────────────────────

_SQL_RE = re.compile(r"```sql\n(.*?)```", re.DOTALL)

mgr = SessionManager()


def _say(text: str) -> None:
    print(f"\n助手：{text}")


def _extract_sql_blocks(action: str) -> list[str]:
    return _SQL_RE.findall(action)


def _all_unique_placeholders(sqls: list[str]) -> list[str]:
    """回傳所有 SQL 中不重複的佔位符，保持首次出現順序。"""
    seen: set[str] = set()
    result: list[str] = []
    for sql in sqls:
        for p in extract_sql_placeholders(sql):
            if p not in seen:
                seen.add(p)
                result.append(p)
    return result


def _load_case(session: dict[str, Any]) -> tuple[dict, dict]:
    sop_data = load_sop_file(
        str(Path(config.SOP_DIR) / session["current_sop_file"])
    )
    case = sop_data["cases"][session["current_case_id"]]
    return sop_data, case


# ── State handlers ─────────────────────────────────────────────────────────────

def _enter_case(session_id: str, session: dict[str, Any]) -> None:
    """載入當前 case 並開始處理。不需要用戶輸入即可執行。"""
    sop_data, case = _load_case(session)

    # 顯示 question（若非 omit）
    q = case["question"].strip()
    if q and q.lower() != "omit":
        _say(q)

    # 建立 SQL queue
    sql_blocks = _extract_sql_blocks(case["action"])
    mgr.update_session(session_id, {
        "state": "collecting_params",
        "sql_queue": sql_blocks,
        "sql_queue_index": 0,
    })

    if not sql_blocks:
        # 此 case 無 SQL，顯示 action 說明後直接進入條件比對
        _say(f"請依以下步驟操作：\n\n{case['action']}")
        mgr.update_session(session_id, {"state": "matching_case"})
        _do_matching(session_id, session)
        return

    # 立即檢查是否所有參數都已收集（例如 case 跳轉後 collected_params 被清空）
    missing = _all_unique_placeholders(sql_blocks)
    missing = [p for p in missing if not session["collected_params"].get(p)]
    if not missing:
        _do_show_sql(session_id, session)
    else:
        _say(f"需要以下資訊才能執行查詢：\n• {missing[0]}")


def _do_collecting_params(
    session_id: str, session: dict[str, Any], user_input: str
) -> None:
    """用戶回覆參數值 → 提取 → 補問或進入 SQL 確認。"""
    sql_blocks = session["sql_queue"]
    collected = session["collected_params"]
    all_params = _all_unique_placeholders(sql_blocks)
    missing = [p for p in all_params if not collected.get(p)]

    # 嘗試從用戶輸入提取參數
    if user_input and missing:
        extracted = parse_params_from_user_input(user_input, missing)
        new_collected = {**collected, **{k: v for k, v in extracted.items() if v}}
        mgr.update_session(session_id, {"collected_params": new_collected})
        missing = [p for p in all_params if not new_collected.get(p)]

    if not missing:
        _do_show_sql(session_id, session)
    else:
        _say(f"請提供：{missing[0]}")


def _do_show_sql(session_id: str, session: dict[str, Any]) -> None:
    """將下一條 SQL 填入參數並顯示給用戶確認。"""
    sql_blocks = session["sql_queue"]
    idx = session["sql_queue_index"]

    if idx >= len(sql_blocks):
        mgr.update_session(session_id, {"state": "matching_case"})
        _do_matching(session_id, session)
        return

    sql_raw = sql_blocks[idx]
    try:
        sql_filled = fill_sql_params(sql_raw, session["collected_params"])
    except KeyError as e:
        _say(f"參數缺失 {e}，請重新提供。")
        mgr.update_session(session_id, {"state": "collecting_params"})
        return

    mgr.update_session(session_id, {
        "state": "awaiting_sql_confirm",
        "pending_sql": sql_filled,
        "pending_sql_raw": sql_raw,
    })
    _say(
        f"將執行以下查詢，請確認（輸入 yes 確認 / no 取消）：\n\n```sql\n{sql_filled}\n```"
    )


def _do_sql_confirm(
    session_id: str, session: dict[str, Any], user_input: str
) -> None:
    """處理用戶對 SQL 的 yes / no 確認。"""
    ans = user_input.strip().lower()

    if ans == "yes":
        sql = session["pending_sql"]
        try:
            rows = execute_select(sql)
        except DBConnectionError as e:
            _say(str(e))
            mgr.update_session(session_id, {"state": "idle"})
            return
        except SQLRejectedError as e:
            _say(f"SQL 被拒絕：{e}")
            mgr.update_session(session_id, {"state": "idle"})
            return

        # 摘要寫入 known_facts
        summary = f"查詢回傳 {len(rows)} 筆"
        if rows:
            preview = json.dumps(rows[:3], ensure_ascii=False, default=str)
            summary += f"，前 3 筆：{preview}"
        _say(summary)
        mgr.append_known_fact(
            session_id,
            f"{session['current_case_id']} SQL 查詢結果：{summary}",
        )
        mgr.update_session(session_id, {
            "pending_sql": None,
            "pending_sql_raw": None,
            "sql_queue_index": session["sql_queue_index"] + 1,
        })
        # 繼續下一條 SQL 或進入條件比對
        _do_show_sql(session_id, session)

    elif ans == "no":
        _say("已取消 SQL 執行。請問您想如何繼續？（重新描述問題或輸入 exit 離開）")
        mgr.update_session(session_id, {
            "state": "idle",
            "pending_sql": None,
            "pending_sql_raw": None,
        })
    else:
        _say("請輸入 yes 確認執行，或 no 取消。")


def _do_matching(session_id: str, session: dict[str, Any]) -> None:
    """LLM 條件比對，決定跳轉或補問用戶。不需要用戶輸入即可執行。"""
    sop_data, _ = _load_case(session)
    jumps_to: list[str] = sop_data["metadata"].get("jumps_to", [])

    if not jumps_to:
        _say("SOP 流程完成，問題排查結束。如有其他問題請重新描述症狀。")
        mgr.update_session(session_id, {"state": "done"})
        return

    candidates = get_case_symptom_summary(sop_data, jumps_to)
    known_facts_text = "\n".join(f"- {f}" for f in session["known_facts"])
    candidates_text = "\n".join(f"{c['case_id']}: {c['symptom']}" for c in candidates)

    prompt = (
        f"[已知狀態]\n{known_facts_text}\n\n"
        f"[候選 case 的 symptom]\n{candidates_text}\n\n"
        "[任務]\n"
        "根據已知狀態，判斷最符合哪個 case 的 symptom。\n"
        "若已知狀態不足以判斷，回傳 ask_user 詢問用戶。\n"
        "只回傳 JSON，不得輸出其他內容。\n\n"
        "輸出格式：\n"
        '{"next_action": "jump_to_case", "target_case_id": "case_X", "reply_to_user": "..."}\n'
        "或\n"
        '{"next_action": "ask_user", "reply_to_user": "..."}\n'
        "或\n"
        '{"next_action": "human_handoff", "reply_to_user": "..."}'
    )

    result = llm_client.chat(
        system=SOP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        expect_json=True,
    )

    action = result.get("next_action", "human_handoff")
    reply = result.get("reply_to_user", "")

    if reply:
        _say(reply)

    valid_ids = [c["case_id"] for c in candidates]

    if action == "jump_to_case":
        target = result.get("target_case_id", "")
        if target in valid_ids:
            mgr.jump_to_case(session_id, target)
            session = mgr.get_session(session_id)   # 取更新後的 session
            _enter_case(session_id, session)
        else:
            _say(f"[系統] 無效跳轉目標 '{target}'，請通知工程師。")
            mgr.update_session(session_id, {"state": "done"})

    elif action == "ask_user":
        mgr.update_session(session_id, {"state": "matching_case"})

    elif action == "human_handoff":
        mgr.update_session(session_id, {"state": "done"})

    else:
        _say("[系統] AI 回應格式異常，請通知工程師。")
        mgr.update_session(session_id, {"state": "done"})


def _do_fallback(
    session_id: str, session: dict[str, Any], user_input: str
) -> None:
    """Fallback 閒聊模式：直接 LLM 自由對話，不使用 JSON 格式。"""
    history = session["conversation_history"][-10:]
    messages = history + [{"role": "user", "content": user_input}]
    reply = llm_client.chat(
        system=FALLBACK_SYSTEM_PROMPT,
        messages=messages,
        expect_json=False,
    )
    _say(str(reply))
    session["conversation_history"].append({"role": "user", "content": user_input})
    session["conversation_history"].append({"role": "assistant", "content": str(reply)})


# ── Main dispatch ──────────────────────────────────────────────────────────────

def process_turn(session_id: str, user_input: str) -> None:
    session = mgr.get_session(session_id)
    state = session["state"]

    # Fallback 模式：每輪重新 route
    if session["mode"] == "fallback_chat" and state == "idle":
        new_mode = route(user_input, session)
        if new_mode == "sop":
            mgr.clear_for_sop_entry(session_id)
            session = mgr.get_session(session_id)
            _say("已找到對應 SOP，開始排查流程。")
            mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
            _enter_case(session_id, session)
        else:
            _do_fallback(session_id, session, user_input)
        return

    if state == "idle":
        mode = route(user_input, session)
        if mode == "fallback_chat":
            _say("目前找不到對應的 SOP，我會盡力協助您。")
            _do_fallback(session_id, session, user_input)
        else:
            mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
            _enter_case(session_id, session)

    elif state == "collecting_params":
        _do_collecting_params(session_id, session, user_input)

    elif state == "awaiting_sql_confirm":
        _do_sql_confirm(session_id, session, user_input)

    elif state == "matching_case":
        mgr.append_known_fact(session_id, f"用戶補充：{user_input}")
        _do_matching(session_id, session)

    elif state == "done":
        _say("開始新一輪問題排查。")
        mgr.reset_session(session_id)
        process_turn(session_id, user_input)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("正在建立 SOP 向量索引（首次啟動需下載模型，請稍候）...")
    try:
        count = vector_search.index_all_sops(config.SOP_DIR)
        print(f"已索引 {count} 個入口 case。")
    except Exception as e:
        print(f"[警告] Vector Search 初始化失敗：{e}")
        print("將以無 SOP 搜尋模式啟動（所有問題進入 fallback 模式）。")

    session_id = mgr.create_session()
    print("\n智能客服已就緒。請描述您遇到的問題（輸入 exit 離開）：")

    while True:
        try:
            user_input = input("\n您：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break

        if not user_input:
            continue
        if user_input.lower() == "exit":
            print("再見！")
            break

        try:
            process_turn(session_id, user_input)
        except Exception as e:
            print(f"\n[系統錯誤] {e}")
            print("已重置 session，請重新描述您的問題。")
            mgr.reset_session(session_id)


if __name__ == "__main__":
    main()
