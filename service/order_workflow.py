"""高风险退款的受控执行：用户确认、幂等、条件更新和审计。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

DEFAULT_ORDER_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "orders.db"))


class RefundWorkflow:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.getenv("ORDER_DB_PATH", DEFAULT_ORDER_DB)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, isolation_level="IMMEDIATE")
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS refund_requests (
                    idempotency_key TEXT PRIMARY KEY,
                    order_no TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    audit_reason TEXT
                )
                """
            )
            connection.commit()

    def confirm_refund(self, order_no: str, user_id: str, idempotency_key: str, reason: str) -> dict:
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        with closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT * FROM refund_requests WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing["order_no"] != order_no or existing["user_id"] != user_id:
                    raise ValueError("idempotency_key_conflict")
                return {"status": existing["status"], "idempotent_replay": True, "order_no": order_no}

            order = connection.execute(
                "SELECT status, cancelable FROM orders WHERE order_no = ? AND user_id = ?",
                (order_no, user_id),
            ).fetchone()
            if order is None:
                raise PermissionError("order_not_found")
            if not order["cancelable"]:
                raise ValueError("order_not_cancelable")

            updated = connection.execute(
                """
                UPDATE orders
                SET status = ?, logistics = ?, cancelable = 0, updated_at = datetime('now')
                WHERE order_no = ? AND user_id = ? AND cancelable = 1
                """,
                ("退款中", "订单已提交退款申请，处理中", order_no, user_id),
            )
            if updated.rowcount != 1:
                raise ValueError("refund_state_changed")
            connection.execute(
                """
                INSERT INTO refund_requests(idempotency_key, order_no, user_id, status, created_at, completed_at, audit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (idempotency_key, order_no, user_id, "submitted", now, now, reason[:500]),
            )
            connection.commit()
        return {"status": "submitted", "idempotent_replay": False, "order_no": order_no}
