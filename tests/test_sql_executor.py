import re
from unittest.mock import MagicMock, patch, mock_open

import pytest
import psycopg2

from agent.sql_executor import execute_select, SQLRejectedError, DBConnectionError, _append_limit


# ── _append_limit ──────────────────────────────────────────────────────────────

def test_append_limit_added_when_absent():
    sql = "SELECT * FROM t WHERE id = 'x'"
    result = _append_limit(sql)
    assert "LIMIT 200" in result


def test_append_limit_not_duplicated():
    sql = "SELECT * FROM t LIMIT 50"
    result = _append_limit(sql)
    assert result.upper().count("LIMIT") == 1
    assert "50" in result


def test_append_limit_case_insensitive():
    sql = "SELECT * FROM t limit 10"
    result = _append_limit(sql)
    assert result.upper().count("LIMIT") == 1


def test_append_limit_strips_trailing_semicolon():
    sql = "SELECT * FROM t;"
    result = _append_limit(sql)
    assert "LIMIT 200" in result
    assert not result.rstrip().endswith(";")


# ── execute_select：非 SELECT 被拒絕 ───────────────────────────────────────────

@patch("builtins.open", mock_open())
def test_reject_insert():
    with pytest.raises(SQLRejectedError):
        execute_select("INSERT INTO t VALUES (1)")


@patch("builtins.open", mock_open())
def test_reject_update():
    with pytest.raises(SQLRejectedError):
        execute_select("UPDATE t SET col = 1")


@patch("builtins.open", mock_open())
def test_reject_drop():
    with pytest.raises(SQLRejectedError):
        execute_select("DROP TABLE t")


@patch("builtins.open", mock_open())
def test_reject_leading_whitespace():
    """前置空白不影響拒絕邏輯。"""
    with pytest.raises(SQLRejectedError):
        execute_select("  \n  DELETE FROM t")


# ── execute_select：正常執行 ───────────────────────────────────────────────────

@patch("builtins.open", mock_open())
@patch("agent.sql_executor.psycopg2.connect")
def test_returns_list_of_dicts(mock_connect):
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = [{"col": "val"}]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_connect.return_value = mock_conn

    result = execute_select("SELECT col FROM t WHERE id = '1'")
    assert result == [{"col": "val"}]


@patch("builtins.open", mock_open())
@patch("agent.sql_executor.psycopg2.connect")
def test_limit_applied_in_executed_sql(mock_connect):
    """實際執行的 SQL 應包含 LIMIT。"""
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = []

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_connect.return_value = mock_conn

    execute_select("SELECT * FROM t")
    executed = mock_cur.execute.call_args[0][0]
    assert "LIMIT 200" in executed.upper()


# ── execute_select：DB 連線失敗 ────────────────────────────────────────────────

@patch("builtins.open", mock_open())
@patch("agent.sql_executor.psycopg2.connect",
       side_effect=psycopg2.OperationalError("connection refused"))
def test_db_connection_error_raises_db_connection_error(mock_connect):
    with pytest.raises(DBConnectionError, match="資料庫暫時無法連線"):
        execute_select("SELECT 1")


# ── audit log ─────────────────────────────────────────────────────────────────

@patch("agent.sql_executor.psycopg2.connect")
def test_audit_log_written_on_success(mock_connect):
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_connect.return_value = mock_conn

    m = mock_open()
    with patch("builtins.open", m):
        execute_select("SELECT 1")

    written = "".join(c.args[0] for c in m().write.call_args_list)
    assert "rows:" in written


@patch("builtins.open", mock_open())
def test_audit_log_written_on_rejection():
    m = mock_open()
    with patch("builtins.open", m):
        with pytest.raises(SQLRejectedError):
            execute_select("DELETE FROM t")

    written = "".join(c.args[0] for c in m().write.call_args_list)
    assert "REJECTED" in written
