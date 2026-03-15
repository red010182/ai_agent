from unittest.mock import patch

import pytest

from agent.param_extractor import extract_missing_params, parse_params_from_user_input


# ── extract_missing_params ─────────────────────────────────────────────────────

def test_all_params_missing():
    sql = "WHERE equipment_id = '{equipment_id}' AND t > '{start_time}'"
    assert extract_missing_params(sql, {}) == ["equipment_id", "start_time"]


def test_some_params_collected():
    sql = "WHERE equipment_id = '{equipment_id}' AND t > '{start_time}'"
    collected = {"equipment_id": "EQ-001", "start_time": None}
    missing = extract_missing_params(sql, collected)
    assert missing == ["start_time"]


def test_all_params_collected():
    sql = "WHERE equipment_id = '{equipment_id}'"
    collected = {"equipment_id": "EQ-001"}
    assert extract_missing_params(sql, collected) == []


def test_no_placeholders():
    assert extract_missing_params("SELECT 1", {}) == []


def test_empty_string_treated_as_missing():
    """空字串視為未收集。"""
    sql = "WHERE id = '{equipment_id}'"
    assert extract_missing_params(sql, {"equipment_id": ""}) == ["equipment_id"]


# ── parse_params_from_user_input ───────────────────────────────────────────────

@patch("agent.param_extractor.llm_client.chat")
def test_parse_extracts_values(mock_chat):
    mock_chat.return_value = {"equipment_id": "EQ-4721", "start_time": "2024-01-01"}
    result = parse_params_from_user_input(
        "設備是 EQ-4721，從 2024-01-01 開始查", ["equipment_id", "start_time"]
    )
    assert result == {"equipment_id": "EQ-4721", "start_time": "2024-01-01"}


@patch("agent.param_extractor.llm_client.chat")
def test_parse_returns_none_for_unfound(mock_chat):
    """LLM 找不到的參數，value 應為 None。"""
    mock_chat.return_value = {"equipment_id": "EQ-001", "start_time": None}
    result = parse_params_from_user_input("設備是 EQ-001", ["equipment_id", "start_time"])
    assert result["equipment_id"] == "EQ-001"
    assert result["start_time"] is None


@patch("agent.param_extractor.llm_client.chat")
def test_parse_filters_extra_llm_keys(mock_chat):
    """LLM 多回傳的 key 不應出現在結果中。"""
    mock_chat.return_value = {
        "equipment_id": "EQ-001",
        "unexpected_key": "some_value",
    }
    result = parse_params_from_user_input("設備 EQ-001", ["equipment_id"])
    assert "unexpected_key" not in result
    assert result == {"equipment_id": "EQ-001"}


@patch("agent.param_extractor.llm_client.chat")
def test_parse_human_handoff_returns_all_none(mock_chat):
    """LLM 回傳 human_handoff 時，所有參數應為 None。"""
    mock_chat.return_value = {
        "next_action": "human_handoff",
        "reply_to_user": "系統錯誤",
    }
    result = parse_params_from_user_input("不明輸入", ["equipment_id", "start_time"])
    assert result == {"equipment_id": None, "start_time": None}


@patch("agent.param_extractor.llm_client.chat")
def test_parse_empty_missing_list(mock_chat):
    """missing 為空時，不呼叫 LLM，直接回傳空 dict。"""
    result = parse_params_from_user_input("任何輸入", [])
    mock_chat.assert_not_called()
    assert result == {}
