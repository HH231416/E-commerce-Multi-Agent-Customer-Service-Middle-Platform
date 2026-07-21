import os
import sqlite3
import tempfile
import unittest

from service.order_workflow import RefundWorkflow


class RefundWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "orders.db")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                CREATE TABLE orders (
                    order_no TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL,
                    items TEXT NOT NULL, amount REAL NOT NULL, logistics TEXT,
                    cancelable INTEGER NOT NULL, updated_at TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO orders VALUES ('A1001', 'user-1', '待发货', '商品', 99, '', 1, '')"
            )
            connection.commit()
        finally:
            connection.close()
        self.workflow = RefundWorkflow(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_confirm_refund_is_idempotent_and_changes_state_once(self):
        first = self.workflow.confirm_refund("A1001", "user-1", "refund-request-0001", "不再需要")
        second = self.workflow.confirm_refund("A1001", "user-1", "refund-request-0001", "不再需要")

        self.assertEqual("submitted", first["status"])
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        connection = sqlite3.connect(self.db_path)
        try:
            order = connection.execute("SELECT status, cancelable FROM orders WHERE order_no = 'A1001'").fetchone()
        finally:
            connection.close()
        self.assertEqual(("退款中", 0), order)

    def test_confirm_refund_rejects_another_user(self):
        with self.assertRaises(PermissionError):
            self.workflow.confirm_refund("A1001", "user-2", "refund-request-0002", "不再需要")


if __name__ == "__main__":
    unittest.main()
