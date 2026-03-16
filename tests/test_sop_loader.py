import pytest
from agent.sop_loader import (
    load_sop_file,
    get_case,
    get_case_symptom_summary,
    extract_sql_placeholders,
    fill_sql_params,
)

SOP_FILE = "sop/productivity_lost.md"


@pytest.fixture
def sop():
    return load_sop_file(SOP_FILE)


# ── load_sop_file ──────────────────────────────────────────────────────────────

def test_load_scenario(sop):
    assert sop["metadata"]["scenario"] == "productivity_lost"


def test_load_cases_array_in_metadata(sop):
    cases_meta = sop["metadata"]["cases"]
    assert isinstance(cases_meta, list)
    case_ids = [c["case_id"] for c in cases_meta]
    assert "case_1" in case_ids
    assert "case_2" in case_ids
    assert "case_3" in case_ids


def test_load_all_cases(sop):
    assert set(sop["cases"].keys()) == {"case_1", "case_2", "case_3"}


def test_no_is_entry_field(sop):
    """新格式不含 is_entry，所有 case 都是潛在入口。"""
    for case in sop["cases"].values():
        assert "is_entry" not in case


def test_case_title_from_front_matter(sop):
    assert sop["cases"]["case_1"]["title"] == "Tool Scanner Lost"


def test_case_keywords_from_front_matter(sop):
    keywords = sop["cases"]["case_1"]["keywords"]
    assert isinstance(keywords, list)
    assert len(keywords) > 0


def test_case_jumps_to_from_front_matter(sop):
    jumps = sop["cases"]["case_1"]["jumps_to"]
    assert "case_2" in jumps
    assert "case_3" in jumps


def test_symptom_parsed(sop):
    assert sop["cases"]["case_1"]["symptom"] != ""
    assert "xxx issue" in sop["cases"]["case_1"]["symptom"]


def test_problem_to_verify_parsed(sop):
    assert sop["cases"]["case_1"]["problem_to_verify"] != ""


def test_how_to_verify_parsed(sop):
    assert "equipment_id" in sop["cases"]["case_1"]["how_to_verify"]


def test_no_question_or_action_fields(sop):
    """舊欄位 question / action 不應存在。"""
    for case in sop["cases"].values():
        assert "question" not in case
        assert "action" not in case


# ── get_case ───────────────────────────────────────────────────────────────────

def test_get_case_returns_markdown(sop):
    raw = get_case(sop, "case_1")
    assert raw.startswith("## case 1")


def test_get_case_unknown_raises(sop):
    with pytest.raises(KeyError):
        get_case(sop, "case_99")


# ── get_case_symptom_summary ───────────────────────────────────────────────────

def test_symptom_summary_only_returns_id_and_symptom(sop):
    summaries = get_case_symptom_summary(sop, ["case_2", "case_3"])
    assert len(summaries) == 2
    for s in summaries:
        assert set(s.keys()) == {"case_id", "symptom"}


def test_symptom_summary_no_how_to_verify_leak(sop):
    summaries = get_case_symptom_summary(sop, ["case_2"])
    assert "how_to_verify" not in summaries[0]
    assert "note" not in summaries[0]


def test_symptom_summary_unknown_id_skipped(sop):
    summaries = get_case_symptom_summary(sop, ["case_99"])
    assert summaries == []


# ── extract_sql_placeholders ───────────────────────────────────────────────────

def test_extract_placeholders_basic():
    sql = "SELECT * FROM t WHERE id = &equipment_id AND t > &start_time"
    assert extract_sql_placeholders(sql) == ["equipment_id", "start_time"]


def test_extract_placeholders_deduplicated():
    sql = "SELECT &x, &x, &y"
    assert extract_sql_placeholders(sql) == ["x", "y"]


def test_extract_placeholders_none():
    assert extract_sql_placeholders("SELECT 1") == []


# ── fill_sql_params ────────────────────────────────────────────────────────────

def test_fill_params_basic():
    sql = "SELECT * FROM t WHERE id = &equipment_id"
    result = fill_sql_params(sql, {"equipment_id": "EQ-001"})
    assert result == "SELECT * FROM t WHERE id = EQ-001"


def test_fill_params_multiple():
    sql = "WHERE id = &equipment_id AND t > &start_time"
    result = fill_sql_params(sql, {"equipment_id": "EQ-1", "start_time": "2024-01-01"})
    assert "EQ-1" in result
    assert "2024-01-01" in result


def test_fill_params_missing_raises():
    sql = "WHERE id = &equipment_id"
    with pytest.raises(KeyError, match="equipment_id"):
        fill_sql_params(sql, {})
