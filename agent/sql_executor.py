import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


class SQLRejectedError(ValueError):
    """非 SELECT 語句被拒絕時拋出。"""


class DBConnectionError(RuntimeError):
    """資料庫連線失敗時拋出。"""


class SQLExecutionError(RuntimeError):
    """SQL 執行期間發生 DB 錯誤（語法錯誤、欄位不存在等）時拋出。"""

    def __init__(self, error_message: str, sql: str) -> None:
        super().__init__(error_message)
        self.error_message = error_message
        self.sql = sql


def _append_limit(sql: str) -> str:
    """若 SQL 未包含 LIMIT，自動加上 LIMIT 200。"""
    if _LIMIT_RE.search(sql):
        return sql
    return sql.rstrip().rstrip(";") + "\nLIMIT 200"


def _write_audit(sql: str, row_count: int, error: str | None = None) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    status = f"error: {error}" if error else f"rows: {row_count}"
    line = f"[{ts}] {status} | {sql.strip()}\n"
    try:
        with open(config.AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("Failed to write audit log: %s", e)


def execute_select(sql: str) -> list[dict[str, Any]]:
    """執行 SELECT 查詢並回傳結果。

    - 非 SELECT 語句直接拒絕（寫入 audit log）
    - 自動補 LIMIT 200
    - DB 連線失敗拋出 DBConnectionError（不 crash）
    - 結果與 SQL 寫入 audit log
    """
    if not sql.strip().upper().startswith("SELECT"):
        _write_audit(sql, 0, error="REJECTED: not a SELECT statement")
        raise SQLRejectedError("只允許執行 SELECT 語句。")

    sql_with_limit = _append_limit(sql)

    conn = None
    try:
        conn = psycopg2.connect(config.DB_DSN)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_with_limit)
            rows = [dict(row) for row in cur.fetchall()]
        _write_audit(sql_with_limit, len(rows))
        return rows
    except psycopg2.OperationalError as e:
        _write_audit(sql_with_limit, 0, error=str(e))
        raise DBConnectionError("資料庫暫時無法連線，請稍後再試。") from e
    except psycopg2.Error as e:
        error_message = str(e).strip()
        _write_audit(sql_with_limit, 0, error=error_message)
        raise SQLExecutionError(error_message=error_message, sql=sql_with_limit) from e
    finally:
        if conn:
            conn.close()
