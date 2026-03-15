import json
from unittest.mock import MagicMock, patch

import pytest

from agent.llm_client import chat


def _make_response(content: str) -> MagicMock:
    """建立模擬 OpenAI response 物件。"""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@patch("agent.llm_client._get_client")
def test_json_mode_success(mock_get_client):
    payload = {"next_action": "ask_user", "reply_to_user": "請問設備編號？"}
    mock_get_client.return_value.chat.completions.create.return_value = (
        _make_response(json.dumps(payload))
    )

    result = chat("sys", [{"role": "user", "content": "hi"}], expect_json=True)
    assert result == payload


@patch("agent.llm_client._get_client")
def test_json_mode_retry_then_success(mock_get_client):
    """前兩次回傳非 JSON，第三次成功。"""
    good = {"next_action": "ask_user", "reply_to_user": "OK"}
    mock_get_client.return_value.chat.completions.create.side_effect = [
        _make_response("not json"),
        _make_response("still not json"),
        _make_response(json.dumps(good)),
    ]

    result = chat("sys", [{"role": "user", "content": "hi"}], expect_json=True)
    assert result == good
    assert mock_get_client.return_value.chat.completions.create.call_count == 3


@patch("agent.llm_client._get_client")
def test_json_mode_all_fail_returns_human_handoff(mock_get_client):
    """三次都無法 parse，回傳 human_handoff。"""
    mock_get_client.return_value.chat.completions.create.return_value = (
        _make_response("invalid json !!!")
    )

    result = chat("sys", [{"role": "user", "content": "hi"}], expect_json=True)
    assert result["next_action"] == "human_handoff"
    assert "reply_to_user" in result
    assert mock_get_client.return_value.chat.completions.create.call_count == 3


@patch("agent.llm_client._get_client")
def test_fallback_mode_returns_plain_text(mock_get_client):
    mock_get_client.return_value.chat.completions.create.return_value = (
        _make_response("這是一個普通回覆")
    )

    result = chat("sys", [{"role": "user", "content": "hi"}], expect_json=False)
    assert isinstance(result, str)
    assert result == "這是一個普通回覆"


@patch("agent.llm_client._get_client")
def test_fallback_mode_api_error_returns_string(mock_get_client):
    from openai import OpenAIError
    mock_get_client.return_value.chat.completions.create.side_effect = OpenAIError("連線失敗")

    result = chat("sys", [{"role": "user", "content": "hi"}], expect_json=False)
    assert isinstance(result, str)
    assert "無法連線" in result


@patch("agent.llm_client._get_client")
def test_system_prompt_included(mock_get_client):
    """確認 system prompt 有正確傳入。"""
    mock_get_client.return_value.chat.completions.create.return_value = (
        _make_response(json.dumps({"next_action": "ask_user", "reply_to_user": ""}))
    )

    chat("my-system-prompt", [{"role": "user", "content": "hi"}], expect_json=True)
    call_args = mock_get_client.return_value.chat.completions.create.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    assert messages[0] == {"role": "system", "content": "my-system-prompt"}
