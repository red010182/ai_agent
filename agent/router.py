import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from agent import vector_search


def route(user_input: str, session: dict) -> Literal["sop", "fallback_chat"]:
    """決定本輪對話的路由模式。

    只在 session state == "idle" 時呼叫（用戶描述新症狀）。
    case 跳轉後不重新呼叫此函數。

    副作用：
        - 直接更新 session 的 mode / fallback_reason /
          current_sop_file / current_case_id 欄位。
    """
    results = vector_search.search_entry_cases(user_input, top_k=1)

    if not results or results[0].score < config.CONFIDENCE_THRESHOLD:
        session["mode"] = "fallback_chat"
        session["fallback_reason"] = "no_results" if not results else "low_confidence"
        return "fallback_chat"

    session["mode"] = "sop"
    session["fallback_reason"] = None
    session["current_sop_file"] = results[0].sop_file
    session["current_case_id"] = results[0].case_id
    return "sop"
