import pytest
from agent.sop_loader import (
    load_sop_file,
    get_case,
    get_candidate_cases,
    extract_sql_placeholders,
    fill_sql_params,
)

SOP_FILE = "sop/productivity_lost.md"


@pytest.fixture
def sop():
    return load_sop_file(SOP_FILE)


# ── load_sop_file ──────────────────────────────────────────────────────────────

def test_load_metadata(sop):
    assert sop["metadata"]["scenario"] == "productivity_lost"
    assert sop["metadata"]["case_id"] == "case_1"
    assert sop["metadata"]["is_entry"] is True


def test_load_all_cases(sop):
    assert set(sop["cases"].keys()) == {"case_1", "case_2", "case_3"}


def test_entry_case_flag(sop):
    assert sop["cases"]["case_1"]["is_entry"] is True
    assert sop["cases"]["case_2"]["is_entry"] is False
    assert sop["cases"]["case_3"]["is_entry"] is False


def test_symptom_parsed(sop):
    assert "xxx issue" in sop["cases"]["case_1"]["symptom"]


def test_question_parsed(sop):
    assert sop["cases"]["case_1"]["question"] != ""


def test_action_parsed(sop):
    assert "equipment_id" in sop["cases"]["case_1"]["action"]


# ── get_case ───────────────────────────────────────────────────────────────────

def test_get_case_returns_markdown(sop):
    raw = get_case(sop, "case_1")
    assert raw.startswith("## case 1")


def test_get_case_unknown_raises(sop):
    with pytest.raises(KeyError):
        get_case(sop, "case_99")


# ── get_candidate_cases ────────────────────────────────────────────────────────

def test_candidate_cases_only_symptom(sop):
    candidates = get_candidate_cases(sop, ["case_2", "case_3"])
    assert len(candidates) == 2
    for c in candidates:
        assert set(c.keys()) == {"case_id", "symptom"}


def test_candidate_cases_no_action_leak(sop):
    candidates = get_candidate_cases(sop, ["case_2"])
    assert "action" not in candidates[0]
    assert "note" not in candidates[0]


def test_candidate_cases_unknown_id_skipped(sop):
    candidates = get_candidate_cases(sop, ["case_99"])
    assert candidates == []


# ── extract_sql_placeholders ───────────────────────────────────────────────────

def test_extract_placeholders_basic():
    sql = "SELECT * FROM t WHERE id = '{equipment_id}' AND t > '{start_time}'"
    assert extract_sql_placeholders(sql) == ["equipment_id", "start_time"]


def test_extract_placeholders_deduplicated():
    sql = "SELECT '{x}', '{x}', '{y}'"
    assert extract_sql_placeholders(sql) == ["x", "y"]


def test_extract_placeholders_none():
    assert extract_sql_placeholders("SELECT 1") == []


# ── fill_sql_params ────────────────────────────────────────────────────────────

def test_fill_params_basic():
    sql = "SELECT * FROM t WHERE id = '{equipment_id}'"
    result = fill_sql_params(sql, {"equipment_id": "EQ-001"})
    assert result == "SELECT * FROM t WHERE id = 'EQ-001'"


def test_fill_params_multiple():
    sql = "WHERE id = '{equipment_id}' AND t > '{start_time}'"
    result = fill_sql_params(sql, {"equipment_id": "EQ-1", "start_time": "2024-01-01"})
    assert "EQ-1" in result
    assert "2024-01-01" in result


def test_fill_params_missing_raises():
    sql = "WHERE id = '{equipment_id}'"
    with pytest.raises(KeyError, match="equipment_id"):
        fill_sql_params(sql, {})
