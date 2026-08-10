import asyncio
import os
import tempfile
import unittest

from service.ticket_store import TicketStore


class TicketStoreDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "tickets.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_development_store_keeps_tenant_boundary_and_event_flow(self):
        async def scenario():
            tenant_a = TicketStore("tenant-a", self.db_path)
            tenant_b = TicketStore("tenant-b", self.db_path)
            ticket = await tenant_a.create_transfer(
                user_id="user-1",
                reason="用户明确要求转人工",
                summary="需要人工协助",
                context={"sentiment": "normal"},
            )
            assigned = await tenant_a.assign_ticket(ticket["ticket_id"], "agent-1")
            resolved = await tenant_a.resolve_ticket(ticket["ticket_id"], "agent-1", "已完成处理")
            return ticket, assigned, resolved, await tenant_b.get_ticket(ticket["ticket_id"])

        ticket, assigned, resolved, foreign_ticket = asyncio.run(scenario())
        self.assertEqual("pending", ticket["status"])
        self.assertEqual("assigned", assigned["status"])
        self.assertEqual("resolved", resolved["status"])
        self.assertIsNone(foreign_ticket)

    def test_handoff_idempotency_and_open_conversation_reuse(self):
        async def scenario():
            store = TicketStore("tenant-a", self.db_path)
            payload = {"sentiment": "frustrated", "conversation_id": "tenant-a:user-1"}
            first = await store.create_transfer(
                user_id="user-1",
                reason="low_ai_confidence",
                summary="need help",
                context=payload,
                idempotency_key="handoff-request-0001",
            )
            repeated = await store.create_transfer(
                user_id="user-1",
                reason="low_ai_confidence",
                summary="need help",
                context=payload,
                idempotency_key="handoff-request-0001",
            )
            open_ticket = await store.get_open_ticket("user-1", "tenant-a:user-1")
            decision = await store.should_transfer(
                user_id="user-1",
                message="",
                sentiment="frustrated",
                consecutive_failures=2,
            )
            return first, repeated, open_ticket, decision

        first, repeated, open_ticket, decision = asyncio.run(scenario())
        self.assertEqual(first["ticket_id"], repeated["ticket_id"])
        self.assertEqual(first["ticket_id"], open_ticket["ticket_id"])
        self.assertEqual((True, "repeated_unresolved_frustration"), decision)


if __name__ == "__main__":
    unittest.main()
