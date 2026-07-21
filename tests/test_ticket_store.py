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


if __name__ == "__main__":
    unittest.main()
