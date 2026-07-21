"""Locust 压测脚本 — 模拟多用户并发对话"""
import random
from locust import HttpUser, task, between


class CustomerServiceUser(HttpUser):
    """模拟电商客服用户"""
    wait_time = between(3, 10)

    SCENARIOS = {
        "order_query": [
            {"message": "查询订单 ORD001", "user_id": "1"},
            {"message": "我的订单 ORD003 到哪了", "user_id": "3"},
            {"message": "ORD005 能取消吗", "user_id": "5"},
            {"message": "我的订单 ORD002 状态", "user_id": "2"},
            {"message": "查询 ORD008", "user_id": "8"},
        ],
        "product_search": [
            {"message": "有没有键盘推荐", "user_id": "1"},
            {"message": "洗衣机多少钱", "user_id": "2"},
            {"message": "推荐一款鼠标", "user_id": "3"},
            {"message": "搜索蓝牙耳机", "user_id": "4"},
            {"message": "夏科键鼠套装怎么样", "user_id": "6"},
        ],
        "policy_rag": [
            {"message": "退货有什么政策", "user_id": "1"},
            {"message": "退款多久到账", "user_id": "2"},
            {"message": "怎么取消订单", "user_id": "7"},
            {"message": "7天无理由退货条件", "user_id": "5"},
        ],
        "greeting": [
            {"message": "你好", "user_id": "1"},
            {"message": "谢谢", "user_id": "2"},
            {"message": "在吗", "user_id": "3"},
            {"message": "早上好", "user_id": "4"},
        ],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = str(random.randint(1, 10))

    @task(4)
    def order_query(self):
        scenario = random.choice(self.SCENARIOS["order_query"])
        self._send(scenario["message"])

    @task(3)
    def product_search(self):
        scenario = random.choice(self.SCENARIOS["product_search"])
        self._send(scenario["message"])

    @task(2)
    def policy_query(self):
        scenario = random.choice(self.SCENARIOS["policy_rag"])
        self._send(scenario["message"])

    @task(1)
    def greeting(self):
        scenario = random.choice(self.SCENARIOS["greeting"])
        self._send(scenario["message"])

    def _send(self, message):
        """Send message to stream endpoint with timeout"""
        with self.client.post(
            f"/api/chat/{self.user_id}/stream",
            json={"message": message},
            catch_response=True,
            timeout=30,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
            elif response.elapsed.total_seconds() > 15:
                response.failure(f"Timeout: {response.elapsed.total_seconds():.1f}s")
            else:
                response.success()
