"""工单存储门面：生产使用 PostgreSQL，开发与测试保留 SQLite Mock。"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from agents.human_handoff import HumanHandoffManager
from service.database import get_database_engine
from service.settings import settings


class TicketStore:
    TRANSFER_KEYWORDS = HumanHandoffManager.TRANSFER_KEYWORDS
    HIGH_RISK_TAGS = {"payment", "refund", "complaint", "privacy"}

    def __init__(self, tenant_id: str, db_path: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.db_path = db_path
        self._local: HumanHandoffManager | None = None

    @property
    def _uses_postgres(self) -> bool:
        return settings.is_production

    def _sqlite(self) -> HumanHandoffManager:
        if self._local is None:
            self._local = HumanHandoffManager(tenant_id=self.tenant_id, db_path=self.db_path)
        return self._local

    async def should_transfer(
        self,
        user_id: str,
        message: str,
        sentiment: str = "normal",
        ai_confidence: str = "high",
        consecutive_failures: int = 0,
        risk_tags: list[str] | None = None,
    ) -> tuple[bool, str]:
        del user_id
        if any(keyword in message for keyword in self.TRANSFER_KEYWORDS):
            return True, "user_requested_human"
        if sentiment == "angry":
            return True, "high_negative_sentiment"
        if set(risk_tags or ()) & self.HIGH_RISK_TAGS:
            return True, "high_risk_business_intent"
        if sentiment == "frustrated" and consecutive_failures >= 2:
            return True, "repeated_unresolved_frustration"
        if ai_confidence == "low":
            return True, "low_ai_confidence"
        return False, ""

    async def create_transfer(
        self,
        *,
        user_id: str,
        reason: str,
        summary: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self._uses_postgres:
            return await self._sqlite().create_transfer(
                user_id, reason, summary, context, idempotency_key=idempotency_key
            )

        engine = get_database_engine()
        if engine is None:
            raise RuntimeError("生产工单存储未配置 PostgreSQL")
        payload = context or {}
        if idempotency_key:
            async with engine.connect() as connection:
                existing = (
                    await connection.execute(
                        text(
                            "SELECT * FROM support_tickets WHERE tenant_id = :tenant_id "
                            "AND idempotency_key = :idempotency_key"
                        ),
                        {"tenant_id": self.tenant_id, "idempotency_key": idempotency_key},
                    )
                ).mappings().first()
            if existing:
                return self._serialize(dict(existing))
        ticket_id = f"TK-{uuid.uuid4().hex[:16].upper()}"
        now = datetime.now(UTC)
        ticket = {
            "ticket_id": ticket_id,
            "tenant_id": self.tenant_id,
            "user_id": user_id,
            "reason": reason,
            "summary": summary,
            "context": payload,
            "status": "pending",
            "priority": self._priority(reason, payload.get("sentiment")),
            "assigned_to": None,
            "resolution_note": None,
            "source": payload.get("source", "chat"),
            "sentiment": payload.get("sentiment", "normal"),
            "idempotency_key": idempotency_key,
            "conversation_id": payload.get("conversation_id"),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "assigned_at": None,
            "resolved_at": None,
        }
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants(tenant_id, name) VALUES (:tenant_id, :name) "
                    "ON CONFLICT (tenant_id) DO NOTHING"
                ),
                {"tenant_id": self.tenant_id, "name": self.tenant_id},
            )
            inserted = (
                await connection.execute(
                text(
                    """
                    INSERT INTO support_tickets(
                        ticket_id, tenant_id, user_id, status, priority, summary, context_json,
                        reason, source, sentiment, idempotency_key, conversation_id, created_at, updated_at
                    ) VALUES (
                        :ticket_id, :tenant_id, :user_id, :status, :priority, :summary,
                        CAST(:context_json AS JSONB), :reason, :source, :sentiment, :idempotency_key,
                        :conversation_id, :created_at, :updated_at
                    ) ON CONFLICT (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING *
                    """
                ),
                {**ticket, "context_json": json.dumps(payload, ensure_ascii=False)},
            )
            ).mappings().first()
            if inserted is None:
                duplicate = (
                    await connection.execute(
                        text(
                            "SELECT * FROM support_tickets WHERE tenant_id = :tenant_id "
                            "AND idempotency_key = :idempotency_key"
                        ),
                        {"tenant_id": self.tenant_id, "idempotency_key": idempotency_key},
                    )
                ).mappings().first()
                if duplicate is not None:
                    return self._serialize(dict(duplicate))
                raise RuntimeError("Unable to create handoff ticket")
            await self._append_event(connection, ticket_id, "created", user_id, {"reason": reason})
        return ticket

    async def get_open_ticket(self, user_id: str, conversation_id: str | None = None) -> dict[str, Any] | None:
        if not self._uses_postgres:
            return self._sqlite().get_open_ticket(user_id, conversation_id)
        engine = get_database_engine()
        if engine is None:
            raise RuntimeError("PostgreSQL ticket store is not configured")
        clause = " AND (conversation_id = :conversation_id OR conversation_id IS NULL)" if conversation_id else ""
        params: dict[str, Any] = {"tenant_id": self.tenant_id, "user_id": user_id}
        if conversation_id:
            params["conversation_id"] = conversation_id
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT * FROM support_tickets WHERE tenant_id = :tenant_id AND user_id = :user_id "
                        "AND status IN ('pending', 'assigned')" + clause + " ORDER BY created_at DESC LIMIT 1"
                    ),
                    params,
                )
            ).mappings().first()
        return self._serialize(dict(row)) if row else None

    async def list_tickets(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not self._uses_postgres:
            return self._sqlite().list_tickets(status=status, limit=limit)
        engine = get_database_engine()
        if engine is None:
            raise RuntimeError("生产工单存储未配置 PostgreSQL")
        clause = " AND status = :status" if status else ""
        params = {"tenant_id": self.tenant_id, "limit": limit}
        if status:
            params["status"] = status
        query = text(
            "SELECT * FROM support_tickets WHERE tenant_id = :tenant_id"
            f"{clause} ORDER BY created_at DESC LIMIT :limit"
        )
        async with engine.connect() as connection:
            rows = (await connection.execute(query, params)).mappings().all()
        return [self._serialize(dict(row)) for row in rows]

    async def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        if not self._uses_postgres:
            return self._sqlite().get_ticket(ticket_id)
        engine = get_database_engine()
        if engine is None:
            raise RuntimeError("生产工单存储未配置 PostgreSQL")
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT * FROM support_tickets WHERE ticket_id = :ticket_id AND tenant_id = :tenant_id"),
                    {"ticket_id": ticket_id, "tenant_id": self.tenant_id},
                )
            ).mappings().first()
        return self._serialize(dict(row)) if row else None

    async def get_pending_count(self) -> int:
        if not self._uses_postgres:
            return self._sqlite().get_pending_count()
        engine = get_database_engine()
        if engine is None:
            raise RuntimeError("生产工单存储未配置 PostgreSQL")
        async with engine.connect() as connection:
            value = await connection.scalar(
                text("SELECT COUNT(*) FROM support_tickets WHERE tenant_id = :tenant_id AND status = 'pending'"),
                {"tenant_id": self.tenant_id},
            )
        return int(value or 0)

    async def assign_ticket(self, ticket_id: str, agent_id: str) -> dict[str, Any]:
        if not self._uses_postgres:
            return self._sqlite().assign_ticket(ticket_id, agent_id)
        ticket = await self.get_ticket(ticket_id)
        if ticket is None:
            raise ValueError("ticket_not_found")
        if ticket["status"] == "resolved":
            raise ValueError("ticket_already_resolved")
        engine = get_database_engine()
        assert engine is not None
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        UPDATE support_tickets
                        SET status = 'assigned', assigned_to = :agent_id, assigned_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE ticket_id = :ticket_id AND tenant_id = :tenant_id AND status <> 'resolved'
                        RETURNING *
                        """
                    ),
                    {"ticket_id": ticket_id, "tenant_id": self.tenant_id, "agent_id": agent_id},
                )
            ).mappings().first()
            if row is None:
                raise ValueError("ticket_state_changed")
            await self._append_event(connection, ticket_id, "assigned", agent_id, {})
        return self._serialize(dict(row))

    async def resolve_ticket(self, ticket_id: str, agent_id: str, resolution_note: str) -> dict[str, Any]:
        if not self._uses_postgres:
            return self._sqlite().resolve_ticket(ticket_id, agent_id, resolution_note)
        ticket = await self.get_ticket(ticket_id)
        if ticket is None:
            raise ValueError("ticket_not_found")
        engine = get_database_engine()
        assert engine is not None
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        UPDATE support_tickets
                        SET status = 'resolved', assigned_to = COALESCE(assigned_to, :agent_id),
                            resolution_note = :resolution_note, resolved_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE ticket_id = :ticket_id AND tenant_id = :tenant_id
                        RETURNING *
                        """
                    ),
                    {
                        "ticket_id": ticket_id,
                        "tenant_id": self.tenant_id,
                        "agent_id": agent_id,
                        "resolution_note": resolution_note,
                    },
                )
            ).mappings().first()
            if row is None:
                raise ValueError("ticket_not_found")
            await self._append_event(connection, ticket_id, "resolved", agent_id, {"resolution_note": resolution_note})
        return self._serialize(dict(row))

    async def _append_event(self, connection, ticket_id: str, event_type: str, actor_id: str | None, payload: dict) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO ticket_events(event_id, ticket_id, tenant_id, event_type, actor_id, payload)
                VALUES (:event_id, :ticket_id, :tenant_id, :event_type, :actor_id, CAST(:payload AS JSONB))
                """
            ),
            {
                "event_id": f"TE-{uuid.uuid4().hex[:16].upper()}",
                "ticket_id": ticket_id,
                "tenant_id": self.tenant_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )

    @staticmethod
    def _priority(reason: str, sentiment: str | None) -> str:
        if sentiment == "angry" or reason in {"high_negative_sentiment", "high_risk_business_intent"}:
            return "high"
        if sentiment == "frustrated" or reason in {"low_ai_confidence", "repeated_unresolved_frustration"}:
            return "medium"
        return "normal"

    @staticmethod
    def _serialize(row: dict[str, Any]) -> dict[str, Any]:
        context = row.get("context_json", {})
        if isinstance(context, str):
            context = json.loads(context)
        for name in ("created_at", "updated_at", "assigned_at", "resolved_at"):
            if row.get(name) is not None and hasattr(row[name], "isoformat"):
                row[name] = row[name].isoformat()
        return {
            "ticket_id": row["ticket_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "reason": row["reason"],
            "summary": row["summary"],
            "context": context,
            "status": row["status"],
            "priority": row["priority"],
            "assigned_to": row.get("assigned_to"),
            "resolution_note": row.get("resolution_note"),
            "source": row.get("source"),
            "sentiment": row.get("sentiment"),
            "idempotency_key": row.get("idempotency_key"),
            "conversation_id": row.get("conversation_id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "assigned_at": row.get("assigned_at"),
            "resolved_at": row.get("resolved_at"),
        }
