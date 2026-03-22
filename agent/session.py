import uuid
from copy import deepcopy
from typing import Any


def _default_state() -> dict[str, Any]:
    return {
        "current_sop_file": None,
        "current_case_id": None,
        "mode": "idle",               # "idle" | "sop" | "fallback_chat"
        "fallback_reason": None,      # "no_results" | "low_confidence"
        "known_facts": [],            # 跨 case 保留，只能透過 append_known_fact 新增
        "conversation_history": [],   # 最近對話（由呼叫方自行控制長度）
        "collected_params": {},       # case 跳轉時清空
        "pending_sql": None,          # 填入參數後的 SQL，等待用戶確認
        "pending_sql_raw": None,      # 填入前的原始 SQL template
        "sql_queue": [],              # 當前 case 的所有 SQL blocks
        "sql_queue_index": 0,         # 目前執行到第幾條 SQL
        "state": "idle",              # idle | collecting_params | awaiting_sql_confirm
                                      #       | matching_case | ambiguous_case
                                      #       | clarifying | done
        "ambiguous_case_candidates": [],  # [{case_id, title, symptom, sop_file}, ...]
        "visited_cases": {},          # {case_id: visit_count}，用於迴圈偵測
        "max_case_visits": 2,         # 同一 case 最多進入次數
        "clarify_context": None,      # 觸發 clarify 的決策點，e.g. "matching_case"
    }


class SessionManager:
    """管理多用戶 session，以 session_id（UUID）為 key。"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def create_session(self) -> str:
        """建立新 session，回傳 session_id。"""
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = _default_state()
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any]:
        """取得 session dict（回傳內部參考，修改會直接反映）。"""
        if session_id not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found.")
        return self._sessions[session_id]

    def update_session(self, session_id: str, updates: dict[str, Any]) -> None:
        """批次更新 session 欄位。

        禁止直接覆蓋 known_facts，請使用 append_known_fact()。
        """
        if "known_facts" in updates:
            raise ValueError(
                "known_facts 不可直接賦值，請使用 append_known_fact()。"
            )
        session = self.get_session(session_id)
        session.update(updates)

    def append_known_fact(self, session_id: str, fact: str) -> None:
        """將一條已知事實追加到 known_facts（唯一合法的新增方式）。"""
        session = self.get_session(session_id)
        session["known_facts"].append(fact)

    def jump_to_case(
        self, session_id: str, new_case_id: str, new_sop_file: str | None = None
    ) -> None:
        """跳轉至另一個 case。

        規則：保留 collected_params（同名參數自動複用，避免重複填寫），
              清空 pending_sql 與 sql_queue，保留 known_facts。
        """
        session = self.get_session(session_id)
        session["current_case_id"] = new_case_id
        if new_sop_file is not None:
            session["current_sop_file"] = new_sop_file
        session["pending_sql"] = None
        session["pending_sql_raw"] = None
        session["sql_queue"] = []
        session["sql_queue_index"] = 0
        session["state"] = "collecting_params"

    def record_case_visit(self, session_id: str, case_id: str) -> bool:
        """記錄進入 case 的次數。回傳 True 表示允許進入，False 表示超過上限（應 human_handoff）。"""
        session = self.get_session(session_id)
        visited = session["visited_cases"]
        visited[case_id] = visited.get(case_id, 0) + 1
        return visited[case_id] <= session["max_case_visits"]

    def clear_for_sop_entry(self, session_id: str) -> None:
        """Fallback → SOP 切換時清空收集狀態與 known_facts，保留 route 設定的欄位。

        spec：切回 SOP 模式時清空 collected_params、pending_sql、known_facts。
        """
        session = self.get_session(session_id)
        session["known_facts"] = []
        session["collected_params"] = {}
        session["pending_sql"] = None
        session["pending_sql_raw"] = None
        session["sql_queue"] = []
        session["sql_queue_index"] = 0
        session["conversation_history"] = []
        session["ambiguous_case_candidates"] = []
        session["visited_cases"] = {}
        session["clarify_context"] = None
        session["state"] = "idle"

    def reset_session(self, session_id: str) -> None:
        """清空 session 所有狀態（保留 session_id 本身），可開始新一輪問答。"""
        if session_id not in self._sessions:
            raise KeyError(f"Session '{session_id}' not found.")
        self._sessions[session_id] = _default_state()
