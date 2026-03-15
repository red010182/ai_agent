import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from agent.sop_loader import extract_sql_placeholders
from agent import llm_client

logger = logging.getLogger(__name__)

_EXTRACT_SYSTEM_PROMPT = """\
你是參數提取助手。
從用戶的自然語言輸入中，提取指定參數的值。
只回傳 JSON，不得輸出其他內容。

輸出格式：
{"param_name_1": "value_1", "param_name_2": "value_2"}

若某個參數在輸入中找不到，對應的 value 填 null。"""


def extract_missing_params(sql: str, collected: dict[str, str | None]) -> list[str]:
    """回傳 SQL 中尚未收集的參數名稱清單。"""
    all_params = extract_sql_placeholders(sql)
    return [p for p in all_params if not collected.get(p)]


def parse_params_from_user_input(
    user_input: str, missing: list[str]
) -> dict[str, str | None]:
    """呼叫 LLM，從自然語言輸入中提取 missing 參數的值。

    Returns:
        dict，key 為參數名稱，value 為提取到的字串或 None（未找到）。
    """
    if not missing:
        return {}

    param_list = "\n".join(f"- {p}" for p in missing)
    user_message = (
        f"需要提取以下參數：\n{param_list}\n\n"
        f"用戶輸入：{user_input}"
    )

    result = llm_client.chat(
        system=_EXTRACT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        expect_json=True,
    )

    # human_handoff fallback：回傳所有參數為 None
    if result.get("next_action") == "human_handoff":
        logger.warning("parse_params_from_user_input: LLM returned human_handoff")
        return {p: None for p in missing}

    # 只回傳 missing 中的參數，過濾 LLM 可能多回傳的雜訊
    return {p: result.get(p) or None for p in missing}
