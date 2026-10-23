"""单机版可持久化执行账本（不是生产级 exactly-once 系统）。

相同 Idempotency-Key + 相同输入：已完成直接返回原结果；
执行中 / 结果未知：拒绝自动重试付费模型；
同一个 Key 对应不同输入：拒绝覆盖。
本模块只存执行元信息，不存 API Key、Prompt、商品图或用户身份。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

VALID_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class InvalidExecutionKey(ValueError):
    pass


class ExecutionConflict(RuntimeError):
    pass


class PaidGenerationLimitExceeded(RuntimeError):
    pass


class ExecutionConfigurationError(RuntimeError):
    pass


def _validate(key: str) -> None:
    if not VALID_KEY.fullmatch(key):
        raise InvalidExecutionKey("Idempotency-Key 只能包含 8-128 位字母、数字、下划线和短横线")


@contextmanager
def _connect():
    path = Path(os.getenv("POSTER_EXECUTION_DB", "data/executions.sqlite3")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS poster_executions (
            idempotency_key TEXT PRIMARY KEY,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','UNKNOWN')),
            result_json TEXT,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            FOREIGN KEY(idempotency_key) REFERENCES poster_executions(idempotency_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paid_generation_events (
            idempotency_key TEXT PRIMARY KEY,
            started_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    try:
        # Backfill completed legacy AI runs once so the new hourly guardrail
        # accounts for recent completed requests already present in this DB.
        with conn:
            migrated = conn.execute(
                "SELECT 1 FROM execution_schema_migrations WHERE version=1"
            ).fetchone()
            if not migrated:
                rows = conn.execute(
                    "SELECT idempotency_key,started_at,result_json FROM poster_executions "
                    "WHERE status='COMPLETED' AND result_json IS NOT NULL"
                ).fetchall()
                for row in rows:
                    try:
                        result = json.loads(row["result_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(result, dict) and result.get("background_mode") == "ai":
                        conn.execute(
                            "INSERT OR IGNORE INTO paid_generation_events(idempotency_key,started_at) "
                            "VALUES(?,?)",
                            (row["idempotency_key"], row["started_at"]),
                        )
                conn.execute(
                    "INSERT OR IGNORE INTO execution_schema_migrations(version,applied_at) VALUES(1,?)",
                    (_time(),),
                )
        with conn:
            yield conn
    finally:
        conn.close()


def _time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _positive_env_limit(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ExecutionConfigurationError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise ExecutionConfigurationError(f"{name} must be between 1 and {maximum}")
    return value


def _event(conn: sqlite3.Connection, key: str, name: str) -> None:
    conn.execute(
        "INSERT INTO execution_events(idempotency_key,event_type,occurred_at) VALUES(?,?,?)",
        (key, name, _time())
    )


def begin(
    key: str,
    request_hash: str,
    *,
    paid: bool = False,
    max_paid_per_hour: int | None = None,
    max_concurrent_paid: int | None = None,
) -> dict[str, Any] | None:
    """Atomically claim a generation and enforce local paid-call guardrails.

    None means the caller acquired execution rights; a dict reuses a completed
    result. Paid limits are reserved in the same SQLite transaction as the claim.
    """
    _validate(key)
    if paid:
        if max_paid_per_hour is None:
            max_paid_per_hour = _positive_env_limit(
                "POSTER_MAX_AI_GENERATIONS_PER_HOUR", default=20, maximum=10000
            )
        if max_concurrent_paid is None:
            max_concurrent_paid = _positive_env_limit(
                "POSTER_MAX_CONCURRENT_AI_GENERATIONS", default=1, maximum=100
            )
        if not 1 <= max_paid_per_hour <= 10000:
            raise ExecutionConfigurationError("max_paid_per_hour must be between 1 and 10000")
        if not 1 <= max_concurrent_paid <= 100:
            raise ExecutionConfigurationError("max_concurrent_paid must be between 1 and 100")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT request_hash,status,result_json FROM poster_executions WHERE idempotency_key=?",
            (key,)
        ).fetchone()
        if row:
            if row["request_hash"] != request_hash:
                raise ExecutionConflict("相同 Idempotency-Key 不允许用于不同的海报输入")
            if row["status"] == "COMPLETED":
                return json.loads(row["result_json"])
            raise ExecutionConflict(
                "任务执行中或结果未知；不会自动重试收费模型。请查询 /api/executions/{key}"
            )

        if paid:
            now_dt = datetime.now(timezone.utc)
            cutoff = (now_dt - timedelta(hours=1)).isoformat(timespec="seconds")
            retention_cutoff = (now_dt - timedelta(days=7)).isoformat(timespec="seconds")
            conn.execute(
                "DELETE FROM paid_generation_events "
                "WHERE started_at < ? AND NOT EXISTS ("
                "SELECT 1 FROM poster_executions p "
                "WHERE p.idempotency_key=paid_generation_events.idempotency_key "
                "AND p.status='RUNNING')",
                (retention_cutoff,),
            )
            active = conn.execute(
                "SELECT COUNT(*) FROM paid_generation_events e "
                "JOIN poster_executions p ON p.idempotency_key=e.idempotency_key "
                "WHERE p.status='RUNNING'"
            ).fetchone()[0]
            if active >= max_concurrent_paid:
                raise PaidGenerationLimitExceeded(
                    "AI 生图已达到本地演示的并发上限；本次未调用模型，请稍后重试"
                )
            recent = conn.execute(
                "SELECT COUNT(*) FROM paid_generation_events WHERE started_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            if recent >= max_paid_per_hour:
                raise PaidGenerationLimitExceeded(
                    "AI 生图已达到每小时请求上限；本次未调用模型，请稍后再试"
                )

        now = _time()
        conn.execute(
            "INSERT INTO poster_executions VALUES(?,?,'RUNNING',NULL,?,?)",
            (key, request_hash, now, now)
        )
        if paid:
            conn.execute(
                "INSERT INTO paid_generation_events(idempotency_key,started_at) VALUES(?,?)",
                (key, now),
            )
        _event(conn, key, "CLAIMED")
    return None


def finish(key: str, result: dict) -> None:
    _validate(key)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE poster_executions SET status='COMPLETED',result_json=?,updated_at=? "
            "WHERE idempotency_key=? AND status='RUNNING'",
            (json.dumps(result, ensure_ascii=False), _time(), key)
        )
        if cursor.rowcount != 1:
            raise ExecutionConflict("任务状态已改变，不能重复完成")
        _event(conn, key, "COMPLETED")


def mark_unknown(key: str) -> None:
    _validate(key)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE poster_executions SET status='UNKNOWN',updated_at=? "
            "WHERE idempotency_key=? AND status='RUNNING'", (_time(), key)
        )
        if cursor.rowcount:
            _event(conn, key, "RESULT_UNKNOWN")


def get(key: str) -> dict:
    _validate(key)
    with _connect() as conn:
        row = conn.execute(
            "SELECT idempotency_key,status,result_json,started_at,updated_at "
            "FROM poster_executions WHERE idempotency_key=?", (key,)
        ).fetchone()
        if not row:
            raise LookupError("执行记录不存在")
        event_rows = conn.execute(
            "SELECT event_type,occurred_at FROM execution_events "
            "WHERE idempotency_key=? ORDER BY event_id LIMIT 100", (key,)
        ).fetchall()
        return {
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "events": [dict(event) for event in event_rows],
        }


def recover_incomplete() -> int:
    """Single-worker startup reconciliation: orphaned RUNNING → UNKNOWN.

    No automatic retry: server may have called a paid model before crashing.
    Must only be used at startup of a SINGLE worker, never alongside active workers.
    """
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT idempotency_key FROM poster_executions WHERE status='RUNNING'"
        ).fetchall()
        for row in rows:
            key = row["idempotency_key"]
            conn.execute(
                "UPDATE poster_executions SET status='UNKNOWN',updated_at=? "
                "WHERE idempotency_key=? AND status='RUNNING'", (_time(), key)
            )
            _event(conn, key, "RECOVERED_AS_UNKNOWN")
        return len(rows)


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    """Return a bounded, privacy-minimized summary of recent execution records."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT idempotency_key,status,started_at,updated_at,result_json "
            "FROM poster_executions "
            "ORDER BY updated_at DESC, started_at DESC, idempotency_key DESC LIMIT ?",
            (limit,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        items.append(
            {
                "idempotency_key": row["idempotency_key"],
                "status": row["status"],
                "started_at": row["started_at"],
                "updated_at": row["updated_at"],
                "poster_id": result.get("poster_id") if result else None,
                "version": result.get("version") if result else None,
                "background_mode": result.get("background_mode") if result else None,
            }
        )
    return items
