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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class InvalidExecutionKey(ValueError):
    pass


class ExecutionConflict(RuntimeError):
    pass


def _validate(key: str) -> None:
    if not VALID_KEY.fullmatch(key):
        raise InvalidExecutionKey("Idempotency-Key 只能包含 8-128 位字母、数字、下划线和短横线")


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
    return conn


def _time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(conn: sqlite3.Connection, key: str, name: str) -> None:
    conn.execute(
        "INSERT INTO execution_events(idempotency_key,event_type,occurred_at) VALUES(?,?,?)",
        (key, name, _time())
    )


def begin(key: str, request_hash: str) -> dict[str, Any] | None:
    """原子预留一次生成。None=取得执行权；dict=直接复用已完成结果。

    如果模型曾经开始执行、但客户端/服务器丢失结果，拒绝自动重新调用。
    """
    _validate(key)
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
        now = _time()
        conn.execute(
            "INSERT INTO poster_executions VALUES(?,?,'RUNNING',NULL,?,?)",
            (key, request_hash, now, now)
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
