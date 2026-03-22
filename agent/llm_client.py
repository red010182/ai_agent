import json
import logging
import sys
from pathlib import Path
from typing import Any, Generator

from openai import OpenAI, OpenAIError

# 讓 config 可以從專案根目錄 import
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
        )
    return _client


def chat(
    system: str,
    messages: list[dict[str, str]],
    expect_json: bool = True,
) -> dict[str, Any] | str:
    """呼叫 LLM。

    Args:
        system: system prompt
        messages: 對話歷史，格式為 [{"role": "user"/"assistant", "content": "..."}]
        expect_json: True → 解析 JSON 並回傳 dict（SOP 模式）
                     False → 直接回傳純文字（Fallback 閒聊模式）

    Returns:
        expect_json=True  → dict（JSON parse 失敗重試 2 次，仍失敗回傳 human_handoff）
        expect_json=False → str
    """
    client = _get_client()
    full_messages = [{"role": "system", "content": system}] + messages

    if not expect_json:
        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=full_messages,
            )
            return response.choices[0].message.content or ""
        except OpenAIError as e:
            logger.error("LLM API error (fallback mode): %s", e)
            return "抱歉，目前無法連線至 AI 服務，請稍後再試。"

    # JSON 模式：最多嘗試 3 次（初次 + 2 次 retry）
    last_content = ""
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=full_messages,
                response_format={"type": "json_object"},
            )
            last_content = response.choices[0].message.content or ""
            return json.loads(last_content)
        except json.JSONDecodeError:
            logger.warning(
                "JSON parse failed (attempt %d/3): %.200s", attempt + 1, last_content
            )
        except OpenAIError as e:
            logger.error("LLM API error (attempt %d/3): %s", attempt + 1, e)

    logger.error("LLM returned non-JSON after 3 attempts, falling back to human_handoff")
    return {
        "next_action": "human_handoff",
        "reply_to_user": "系統發生錯誤，無法解析 AI 回應，請通知工程師處理。",
    }


def select_case(
    user_input: str,
    candidates: list[dict[str, str]],
) -> dict[str, Any]:
    """從候選 case 中選出最符合用戶描述的 case。

    Args:
        user_input: 用戶原始描述
        candidates: [{case_id, title, symptom}, ...]

    Returns:
        confidence=="high": {"chosen_case_id": "case_X", "reason": "...", "confidence": "high"}
        confidence=="low":  {"chosen_case_id": null, "confidence": "low", "reply_to_user": "..."}
    """
    candidates_text = "\n".join(
        f"{c['case_id']}: {c['symptom']}" for c in candidates
    )
    prompt = (
        f"[用戶描述]\n{user_input}\n\n"
        f"[候選 case 的 symptom]\n{candidates_text}\n\n"
        "[任務]\n"
        "根據用戶描述，選出 symptom 最符合的 case。\n"
        "confidence: \"high\" 若有明確符合，\"low\" 若候選 symptom 差異不大\n\n"
        "輸出（high）: {\"chosen_case_id\": \"case_X\", \"reason\": \"...\", \"confidence\": \"high\"}\n"
        "輸出（low）: {\"chosen_case_id\": null, \"confidence\": \"low\", \"reply_to_user\": \"找到以下幾個可能符合的情況，請選擇最符合的：\"}"
    )
    return chat(
        "你是半導體製程疑難雜症排查助手，負責從候選 case 中選出最符合的一個。"
        "必須以 JSON 格式回覆。若資訊不足以判斷，優先使用 clarify 反問，不得強行猜測。",
        [{"role": "user", "content": prompt}],
        expect_json=True,
    )


def chat_stream(
    system: str,
    messages: list[dict[str, str]],
) -> Generator[str, None, None]:
    """Stream LLM 回覆，逐 chunk yield 文字（僅用於 fallback 閒聊模式）。

    失敗時 yield 錯誤提示字串後結束，不拋出例外。
    """
    client = _get_client()
    full_messages = [{"role": "system", "content": system}] + messages
    try:
        stream = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=full_messages,
            stream=True,
        )
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content
    except OpenAIError as e:
        logger.error("LLM streaming error: %s", e)
        yield "抱歉，目前無法連線至 AI 服務，請稍後再試。"
