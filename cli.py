#!/usr/bin/env python3
"""智能客服 Agent — CLI 入口（單用戶終端模式）。

執行：
    python cli.py
    或
    .venv/bin/python cli.py
"""

import json
import sys
from pathlib import Path
from typing import Any

import config
from agent import llm_client, vector_search
from agent.param_extractor import parse_params_from_user_input
from agent.router import route
from agent.session import SessionManager
from agent.sop_loader import (
    extract_sql_blocks,
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
5. 任何決策點若資訊不足，優先使用 clarify 反問，不得強行猜測，也不得過早 human_handoff

輸出格式請見 [current_task] 的說明。"""

FALLBACK_SYSTEM_PROMPT = "你是一個友善的助手，使用繁體中文回覆。"

# ── Helpers ────────────────────────────────────────────────────────────────────

mgr = SessionManager()


def _say(text: str) -> None:
    print(f"\n助手：{text}")


def _load_case(session: dict[str, Any]) -> tuple[dict, dict]:
    sop_data = load_sop_file(
        str(Path(config.SOP_DIR) / session["current_sop_file"])
    )
    case = sop_data["cases"][session["current_case_id"]]
    return sop_data, case


# ── State handlers ─────────────────────────────────────────────────────────────

def _enter_case(session_id: str, session: dict[str, Any]) -> None:
    """載入當前 case 並開始處理。不需要用戶輸入即可執行。"""
    case_id = session["current_case_id"]

    # 迴圈偵測：同一 case 進入次數超過上限 → 結束
    if not mgr.record_case_visit(session_id, case_id):
        _say(f"⚠️ 偵測到重複進入 {case_id}，可能發生無限迴圈。請通知工程師協助處理。")
        mgr.update_session(session_id, {"state": "done"})
        return

    sop_data, case = _load_case(session)
    metadata = sop_data["metadata"]

    # 提取 how_to_verify 中所有 SQL block，存入 session
    sql_blocks = extract_sql_blocks(case.get("how_to_verify", ""))
    mgr.update_session(session_id, {"sql_blocks": sql_blocks, "current_sql_index": None})

    # 開場說明
    title = case.get("title", case_id)
    problem = case.get("problem_to_verify", "").strip()
    if problem and problem.lower() != "omit":
        _say(f"這看起來是 {case_id}：{title}。\n\n為了驗證【{problem}】，開始執行排查流程。")
    else:
        _say(f"這看起來是 {case_id}：{title}。\n\n開始執行排查流程。")

    mgr.update_session(session_id, {"state": "matching_case"})
    _do_matching(session_id, mgr.get_session(session_id))


def _do_collecting_params(
    session_id: str, session: dict[str, Any], user_input: str
) -> None:
    """用戶回覆參數值 → 提取 → 補問或進入 SQL 確認。"""
    sql_raw = session.get("pending_sql_raw") or ""
    collected = session["collected_params"]
    all_params = extract_sql_placeholders(sql_raw)
    missing = [p for p in all_params if not collected.get(p)]

    # 嘗試從用戶輸入提取參數
    if user_input and missing:
        extracted = parse_params_from_user_input(user_input, missing)
        new_collected = {**collected, **{k: v for k, v in extracted.items() if v}}
        mgr.update_session(session_id, {"collected_params": new_collected})
        missing = [p for p in all_params if not new_collected.get(p)]

    if not missing:
        _do_show_sql(session_id, mgr.get_session(session_id))
    else:
        _say(f"請提供：{missing[0]}")


def _do_show_sql(session_id: str, session: dict[str, Any]) -> None:
    """將 pending_sql_raw 填入參數並顯示給用戶確認。"""
    sql_raw = session.get("pending_sql_raw") or ""
    try:
        sql_filled = fill_sql_params(sql_raw, session["collected_params"])
    except KeyError as e:
        _say(f"參數缺失 {e}，請重新提供。")
        mgr.update_session(session_id, {"state": "collecting_params"})
        return

    mgr.update_session(session_id, {
        "state": "awaiting_sql_confirm",
        "pending_sql": sql_filled,
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
        _do_matching(session_id, mgr.get_session(session_id))

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
    """LLM 決策點：決定執行哪條 SQL、跳轉目標，或反問用戶。不需要用戶輸入即可執行。"""
    sop_data, case = _load_case(session)
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
        "- jump_to_case：說明即將進入哪個 case，標示 case 名稱\n"
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

    result = llm_client.chat(
        system=SOP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        expect_json=True,
    )

    action = result.get("next_action", "human_handoff")
    reply = result.get("reply_to_user", "")
    valid_ids = [c["case_id"] for c in candidates]

    if reply and action != "clarify":
        _say(reply)

    if action == "execute_sql":
        sql_index = result.get("sql_index")
        if sql_index is None or not isinstance(sql_index, int) or sql_index >= len(sql_blocks):
            _say(f"[系統] 無效的 sql_index（{sql_index}），請通知工程師。")
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
            _say(f"即將執行以下查詢：\n\n```sql\n{sql_raw}\n```\n\n需要以下資訊：{missing[0]}")
        else:
            _do_show_sql(session_id, session)

    elif action == "jump_to_case":
        target = result.get("target_case_id", "")
        if target in valid_ids:
            mgr.jump_to_case(session_id, target)
            _enter_case(session_id, mgr.get_session(session_id))
        else:
            _say(f"[系統] 無效跳轉目標 '{target}'，請通知工程師。")
            mgr.update_session(session_id, {"state": "done"})

    elif action == "clarify":
        options = result.get("options", [])
        options_text = "\n".join(f"  {i + 1}. {o}" for i, o in enumerate(options))
        msg = reply
        if options_text:
            msg += f"\n\n選項：\n{options_text}\n\n（或直接輸入說明）"
        _say(msg)
        mgr.update_session(session_id, {
            "state": "clarifying",
            "clarify_context": "matching_case",
        })

    elif action in ("done", "human_handoff"):
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
        _do_matching(session_id, mgr.get_session(session_id))

    elif state == "clarifying":
        mgr.append_known_fact(session_id, f"用戶澄清：{user_input}")
        mgr.update_session(session_id, {"state": "matching_case", "clarify_context": None})
        _do_matching(session_id, mgr.get_session(session_id))

    elif state == "ambiguous_case":
        # 用戶選擇 case：接受 case_id 直接輸入
        candidates = session.get("ambiguous_case_candidates", [])
        valid_ids = [c["case_id"] for c in candidates]
        chosen = user_input.strip()
        if chosen in valid_ids:
            chosen_meta = next(c for c in candidates if c["case_id"] == chosen)
            mgr.update_session(session_id, {
                "current_case_id": chosen,
                "current_sop_file": chosen_meta["sop_file"],
                "ambiguous_case_candidates": [],
            })
            mgr.append_known_fact(session_id, f"原始症狀：{user_input}")
            _enter_case(session_id, mgr.get_session(session_id))
        else:
            ids_str = "、".join(valid_ids)
            _say(f"請輸入有效的 case ID（{ids_str}）。")

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
