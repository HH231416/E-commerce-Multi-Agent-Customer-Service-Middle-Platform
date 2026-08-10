import unittest

from agents.sentiment_model import SentimentDetector


class SentimentRoutingTests(unittest.TestCase):
    def test_analyze_returns_intensity_and_business_risk_tags(self):
        result = SentimentDetector().analyze("支付失败还重复扣款，我要投诉")
        self.assertIn(result["sentiment"], {"frustrated", "angry"})
        self.assertIn("payment", result["risk_tags"])
        self.assertIn("complaint", result["risk_tags"])
        self.assertIn(result["intensity"], {"medium", "high"})

    def test_normal_message_has_no_risk_tag(self):
        result = SentimentDetector().analyze("请问订单什么时候发货")
        self.assertEqual("normal", result["sentiment"])
        self.assertEqual([], result["risk_tags"])


if __name__ == "__main__":
    unittest.main()
