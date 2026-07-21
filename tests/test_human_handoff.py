import asyncio
import os
import tempfile
import unittest

from agents.human_handoff import HumanHandoffManager


class HumanHandoffWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "handoff.db")
        self.manager = HumanHandoffManager(redis_client=None, db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_transfer_persists_ticket_and_counts_pending(self):
        ticket = self._run_async(
            self.manager.create_transfer(
                user_id="user-1",
                reason="用户明确要求转人工",
                summary="用户多次催单，情绪激动",
                context={"sentiment": "angry", "source": "stream"},
            )
        )

        stored_ticket = self.manager.get_ticket(ticket["ticket_id"])

        self.assertEqual("pending", stored_ticket["status"])
        self.assertEqual("angry", stored_ticket["context"]["sentiment"])
        self.assertEqual(1, self.manager.get_pending_count())

    def test_assign_and_resolve_ticket_updates_status(self):
        ticket = self._run_async(
            self.manager.create_transfer(
                user_id="user-2",
                reason="AI 置信度不足",
                summary="用户咨询退款规则并要求人工介入",
                context={"sentiment": "frustrated"},
            )
        )

        assigned_ticket = self.manager.assign_ticket(ticket["ticket_id"], agent_id="agent-7")
        resolved_ticket = self.manager.resolve_ticket(
            ticket["ticket_id"],
            agent_id="agent-7",
            resolution_note="已电话解释退款政策并完成安抚",
        )

        self.assertEqual("assigned", assigned_ticket["status"])
        self.assertEqual("agent-7", assigned_ticket["assigned_to"])
        self.assertEqual("resolved", resolved_ticket["status"])
        self.assertEqual("agent-7", resolved_ticket["assigned_to"])
        self.assertEqual("已电话解释退款政策并完成安抚", resolved_ticket["resolution_note"])

    def test_list_tickets_supports_status_filter(self):
        self._run_async(
            self.manager.create_transfer(
                user_id="user-3",
                reason="用户明确要求转人工",
                summary="订单延迟发货",
                context={"sentiment": "normal"},
            )
        )
        ticket = self._run_async(
            self.manager.create_transfer(
                user_id="user-4",
                reason="检测到用户情绪愤怒",
                summary="物流迟迟未更新",
                context={"sentiment": "angry"},
            )
        )
        self.manager.assign_ticket(ticket["ticket_id"], agent_id="agent-9")

        pending_tickets = self.manager.list_tickets(status="pending")
        assigned_tickets = self.manager.list_tickets(status="assigned")

        self.assertEqual(1, len(pending_tickets))
        self.assertEqual("user-3", pending_tickets[0]["user_id"])
        self.assertEqual(1, len(assigned_tickets))
        self.assertEqual("agent-9", assigned_tickets[0]["assigned_to"])

    def test_tenant_cannot_read_another_tenants_ticket(self):
        ticket = self._run_async(
            self.manager.create_transfer(
                user_id="user-5",
                reason="用户明确要求转人工",
                summary="账户问题",
            )
        )
        another_tenant = HumanHandoffManager(
            redis_client=None,
            db_path=self.db_path,
            tenant_id="tenant-b",
        )

        self.assertIsNone(another_tenant.get_ticket(ticket["ticket_id"]))
        self.assertEqual([], another_tenant.list_tickets())

    def _run_async(self, coroutine):
        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
