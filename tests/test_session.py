import pytest
from agent.session import SessionManager


@pytest.fixture
def mgr() -> SessionManager:
    return SessionManager()


@pytest.fixture
def sid(mgr: SessionManager) -> str:
    return mgr.create_session()


# ── create / get ───────────────────────────────────────────────────────────────

def test_create_returns_unique_ids(mgr):
    id1 = mgr.create_session()
    id2 = mgr.create_session()
    assert id1 != id2


def test_get_unknown_session_raises(mgr):
    with pytest.raises(KeyError):
        mgr.get_session("nonexistent-id")


def test_new_session_has_default_state(mgr, sid):
    s = mgr.get_session(sid)
    assert s["state"] == "idle"
    assert s["known_facts"] == []
    assert s["collected_params"] == {}
    assert s["pending_sql"] is None


# ── update_session ─────────────────────────────────────────────────────────────

def test_update_session(mgr, sid):
    mgr.update_session(sid, {"state": "collecting_params", "current_case_id": "case_1"})
    s = mgr.get_session(sid)
    assert s["state"] == "collecting_params"
    assert s["current_case_id"] == "case_1"


def test_update_session_known_facts_raises(mgr, sid):
    """known_facts 不能透過 update_session 直接覆蓋。"""
    with pytest.raises(ValueError, match="known_facts"):
        mgr.update_session(sid, {"known_facts": ["hack"]})


# ── append_known_fact ──────────────────────────────────────────────────────────

def test_append_known_fact(mgr, sid):
    mgr.append_known_fact(sid, "原始症狀：scanner lost")
    mgr.append_known_fact(sid, "case_1 查詢結果：foup_count = 3")
    s = mgr.get_session(sid)
    assert len(s["known_facts"]) == 2
    assert "scanner lost" in s["known_facts"][0]


# ── jump_to_case ───────────────────────────────────────────────────────────────

def test_jump_clears_collected_params(mgr, sid):
    mgr.update_session(sid, {"collected_params": {"equipment_id": "EQ-001"}})
    mgr.jump_to_case(sid, "case_2")
    assert mgr.get_session(sid)["collected_params"] == {}


def test_jump_clears_pending_sql(mgr, sid):
    mgr.update_session(sid, {"pending_sql": "SELECT 1", "pending_sql_raw": "SELECT 1"})
    mgr.jump_to_case(sid, "case_2")
    s = mgr.get_session(sid)
    assert s["pending_sql"] is None
    assert s["pending_sql_raw"] is None


def test_jump_preserves_known_facts(mgr, sid):
    """跨 case 跳轉後 known_facts 必須完整保留。"""
    mgr.append_known_fact(sid, "原始症狀：scanner lost")
    mgr.append_known_fact(sid, "case_1 查詢結果：foup_count = 3")
    mgr.jump_to_case(sid, "case_2")
    facts = mgr.get_session(sid)["known_facts"]
    assert len(facts) == 2
    assert "scanner lost" in facts[0]
    assert "foup_count" in facts[1]


def test_jump_sets_new_case_id(mgr, sid):
    mgr.jump_to_case(sid, "case_3", new_sop_file="productivity_lost.md")
    s = mgr.get_session(sid)
    assert s["current_case_id"] == "case_3"
    assert s["current_sop_file"] == "productivity_lost.md"
    assert s["state"] == "collecting_params"


# ── reset_session ──────────────────────────────────────────────────────────────

def test_reset_clears_all_state(mgr, sid):
    mgr.append_known_fact(sid, "some fact")
    mgr.update_session(sid, {"state": "done", "current_case_id": "case_3"})
    mgr.reset_session(sid)
    s = mgr.get_session(sid)
    assert s["state"] == "idle"
    assert s["known_facts"] == []
    assert s["current_case_id"] is None


def test_reset_unknown_session_raises(mgr):
    with pytest.raises(KeyError):
        mgr.reset_session("ghost-id")


def test_session_id_still_valid_after_reset(mgr, sid):
    mgr.reset_session(sid)
    s = mgr.get_session(sid)  # 不應拋出 KeyError
    assert s is not None
