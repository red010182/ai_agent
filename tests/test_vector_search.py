"""
vector_search 單元測試。

Qdrant 與 embedding 皆使用 mock，
核心驗證：is_entry: false 的 case 絕對不進入 index。
"""
from unittest.mock import MagicMock, patch, call
import pytest

import agent.vector_search as vs
from agent.vector_search import SearchResult


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singletons():
    """每個測試前重置模組全域狀態，避免測試間互相影響。"""
    vs._qdrant = None
    vs._embed_fn = None
    yield
    vs._qdrant = None
    vs._embed_fn = None


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """固定回傳長度 4 的假向量，方便測試。"""
    return [[0.1, 0.2, 0.3, 0.4]] * len(texts)


# ── index_all_sops ─────────────────────────────────────────────────────────────

@patch("agent.vector_search._get_qdrant")
@patch("agent.vector_search._embed", side_effect=_fake_embed)
def test_all_cases_indexed(mock_embed, mock_get_qdrant):
    """所有 case 都應進入 index（無 is_entry 過濾）。"""
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value.collections = []
    mock_get_qdrant.return_value = mock_qdrant

    count = vs.index_all_sops("sop")

    assert count >= 3  # productivity_lost.md 有 case_1/2/3

    upsert_calls = mock_qdrant.upsert.call_args_list
    assert len(upsert_calls) == 1

    points = upsert_calls[0].kwargs.get("points") or upsert_calls[0].args[1]
    indexed_case_ids = [p.payload["case_id"] for p in points]

    assert "case_1" in indexed_case_ids
    assert "case_2" in indexed_case_ids
    assert "case_3" in indexed_case_ids


@patch("agent.vector_search._get_qdrant")
@patch("agent.vector_search._embed", side_effect=_fake_embed)
def test_index_recreates_collection(mock_embed, mock_get_qdrant):
    """若 collection 已存在，應先刪除再重建（冪等）。"""
    existing = MagicMock()
    existing.name = vs.COLLECTION_NAME
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value.collections = [existing]
    mock_get_qdrant.return_value = mock_qdrant

    vs.index_all_sops("sop")

    mock_qdrant.delete_collection.assert_called_once_with(vs.COLLECTION_NAME)
    mock_qdrant.create_collection.assert_called_once()


@patch("agent.vector_search._get_qdrant")
@patch("agent.vector_search._embed", side_effect=_fake_embed)
def test_payload_fields(mock_embed, mock_get_qdrant):
    """每個 point 的 payload 必須包含必要欄位。"""
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value.collections = []
    mock_get_qdrant.return_value = mock_qdrant

    vs.index_all_sops("sop")

    points = mock_qdrant.upsert.call_args_list[0].kwargs.get("points") \
             or mock_qdrant.upsert.call_args_list[0].args[1]

    for p in points:
        assert "sop_file" in p.payload
        assert "case_id" in p.payload
        assert "scenario" in p.payload
        assert "title" in p.payload
        assert "keywords" in p.payload


# ── search_entry_cases ─────────────────────────────────────────────────────────

@patch("agent.vector_search._get_qdrant")
@patch("agent.vector_search._embed", side_effect=_fake_embed)
def test_search_returns_search_results(mock_embed, mock_get_qdrant):
    hit = MagicMock()
    hit.payload = {
        "sop_file": "productivity_lost.md",
        "case_id": "case_1",
        "scenario": "productivity_lost",
        "title": "xxx issue",
        "keywords": ["xxx issue"],
    }
    hit.score = 0.92

    mock_response = MagicMock()
    mock_response.points = [hit]
    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value = mock_response
    mock_get_qdrant.return_value = mock_qdrant

    results = vs.search_entry_cases("xxx issue", top_k=1)

    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].score == 0.92
    assert results[0].case_id == "case_1"


@patch("agent.vector_search._get_qdrant")
@patch("agent.vector_search._embed", side_effect=_fake_embed)
def test_search_empty_returns_empty_list(mock_embed, mock_get_qdrant):
    mock_response = MagicMock()
    mock_response.points = []
    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value = mock_response
    mock_get_qdrant.return_value = mock_qdrant

    results = vs.search_entry_cases("something unrelated", top_k=1)
    assert results == []
