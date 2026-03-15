from unittest.mock import patch

import pytest

from agent.router import route
from agent.vector_search import SearchResult


def _session() -> dict:
    return {"mode": None, "fallback_reason": None, "current_sop_file": None, "current_case_id": None}


def _result(score: float) -> SearchResult:
    return SearchResult(
        sop_file="productivity_lost.md",
        case_id="case_1",
        scenario="productivity_lost",
        title="xxx issue",
        keywords=["xxx issue"],
        score=score,
    )


# ── fallback 路徑 ──────────────────────────────────────────────────────────────

@patch("agent.router.vector_search.search_entry_cases", return_value=[])
def test_no_results_enters_fallback(mock_search):
    session = _session()
    result = route("random query", session)

    assert result == "fallback_chat"
    assert session["mode"] == "fallback_chat"
    assert session["fallback_reason"] == "no_results"


@patch("agent.router.vector_search.search_entry_cases")
def test_low_score_enters_fallback(mock_search):
    mock_search.return_value = [_result(score=0.50)]
    session = _session()
    result = route("vague symptom", session)

    assert result == "fallback_chat"
    assert session["mode"] == "fallback_chat"
    assert session["fallback_reason"] == "low_confidence"


@patch("agent.router.vector_search.search_entry_cases")
def test_score_exactly_at_threshold_enters_sop(mock_search):
    """score 剛好等於閾值（0.70）應進入 SOP 模式（spec: score >= threshold）。"""
    mock_search.return_value = [_result(score=0.70)]
    session = _session()
    result = route("borderline query", session)

    assert result == "sop"


@patch("agent.router.vector_search.search_entry_cases")
def test_score_just_below_threshold_enters_fallback(mock_search):
    """score 略低於閾值（0.699）應進入 fallback。"""
    mock_search.return_value = [_result(score=0.699)]
    session = _session()
    result = route("borderline query", session)

    assert result == "fallback_chat"


# ── SOP 路徑 ───────────────────────────────────────────────────────────────────

@patch("agent.router.vector_search.search_entry_cases")
def test_high_score_enters_sop(mock_search):
    mock_search.return_value = [_result(score=0.91)]
    session = _session()
    result = route("xxx issue 產能下降", session)

    assert result == "sop"
    assert session["mode"] == "sop"
    assert session["current_sop_file"] == "productivity_lost.md"
    assert session["current_case_id"] == "case_1"
    assert session["fallback_reason"] is None


@patch("agent.router.vector_search.search_entry_cases")
def test_sop_mode_sets_correct_sop_file(mock_search):
    mock_search.return_value = [_result(score=0.85)]
    session = _session()
    route("tool offline", session)

    assert session["current_sop_file"] == "productivity_lost.md"
    assert session["current_case_id"] == "case_1"
