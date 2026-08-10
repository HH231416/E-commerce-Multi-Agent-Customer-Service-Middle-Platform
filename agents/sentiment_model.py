"""轻量情感检测 — 关键词+正则规则，不额外调用 LLM

设计意图：
- 零成本检测：纯规则匹配，不消耗任何 LLM Token，延迟 < 1ms
- 前置拦截：在调用 LLM 之前检测愤怒/受挫用户，提前触发转人工
- 为什么不用 LLM 做情感分析：每次用户消息都调 LLM 成本高（每次 ~100 tokens），
    而且客服系统需要快速响应，规则匹配比 LLM 推理快 100 倍以上
- 局限性：规则匹配无法理解反讽、上下文情绪变化，但作为前置过滤器足够
"""
import re  # 正则表达式，用于复杂情感模式匹配
from typing import Any, Dict

class SentimentDetector:
    """
    基于关键词和正则规则的情感快速检测。
    
    情感分级：
    - normal: 正常（无触发词或触发词不足）
    - frustrated: 受挫（少量负面词或特定句式）
    - angry: 愤怒（大量负面词，需要立即转人工）
    """

    # 愤怒关键词：强负面情绪，命中 1 个即为 frustrated，3 个为 angry
    ANGRY_KEYWORDS = [
        "投诉", "退款", "举报", "欺骗", "垃圾", "差评",
        "太慢了", "等了", "到底", "怎么回事", "受不了",
        "敷衍", "推卸", "不处理", "骗子", "曝光",
    ]

    # 受挫关键词：轻度负面情绪，配合其他规则使用
    FRUSTRATED_KEYWORDS = [
        "怎么还", "还没", "又", "能不能", "到底",
        "一直", "老是", "总是", "每次",
    ]

    # 受挫句式正则：匹配"已经问了 N 次了""又出问题了"等反复受挫的表达
    FRUSTRATED_PATTERNS = [
        r"(已经.*次了)",  # "已经问了三次了"
        r"(又.*问题)",  # "又出问题了"
        r"(为什么.*还不能)",  # "为什么还不能退款"
        r"(等.*天了)",  # "等了好几天了"
        r"(每次.*都)",  # "每次都这样"
    ]

    # Sentiment alone is not a reliable proxy for business risk.
    RISK_KEYWORDS = {
        "payment": ("支付失败", "扣款", "重复扣款", "银行卡", "付款"),
        "refund": ("退款", "退钱", "赔付", "理赔"),
        "complaint": ("投诉", "举报", "曝光", "消协", "平台介入"),
        "privacy": ("隐私", "泄露", "身份证", "手机号"),
        "logistics": ("丢件", "损坏", "没收到", "物流异常"),
    }

    def detect(self, text: str) -> str:
        """
        检测用户消息的情感状态。
        
        输入：用户消息文本
        输出："normal" | "frustrated" | "angry"
        
        判定逻辑：
        - angry_count >= 3 → "angry"（直接升级）
        - angry_count >= 1 OR frustrated_count >= 2 → "frustrated"
        - 其他 → "normal"
        """
        angry_count = sum(1 for kw in self.ANGRY_KEYWORDS if kw in text)  # 统计愤怒关键词
        frustrated_count = sum(1 for kw in self.FRUSTRATED_KEYWORDS if kw in text)  # 统计受挫关键词

        for pattern in self.FRUSTRATED_PATTERNS:  # 正则模式匹配
            if re.search(pattern, text):  # 匹配到受挫句式
                frustrated_count += 1  # 受挫计数 +1

        if angry_count >= 3:  # 愤怒阈值：3 个愤怒词
            return "angry"
        if angry_count >= 1 or frustrated_count >= 2:  # 受挫阈值
            return "frustrated"
        return "normal"  # 正常

    def analyze(self, text: str) -> Dict[str, Any]:
        """Return routing metadata without an additional LLM call."""
        normalized = text or ""
        angry_count = sum(1 for kw in self.ANGRY_KEYWORDS if kw in normalized)
        frustrated_count = sum(1 for kw in self.FRUSTRATED_KEYWORDS if kw in normalized)
        pattern_count = sum(1 for pattern in self.FRUSTRATED_PATTERNS if re.search(pattern, normalized))
        risk_tags = [tag for tag, words in self.RISK_KEYWORDS.items() if any(word in normalized for word in words)]
        sentiment = self.detect(normalized)
        score = min(1.0, angry_count * 0.25 + (frustrated_count + pattern_count) * 0.12)
        if sentiment == "angry":
            score = max(score, 0.85)
        elif sentiment == "frustrated":
            score = max(score, 0.5)
        return {
            "sentiment": sentiment,
            "score": round(score, 3),
            "intensity": "high" if score >= 0.8 else "medium" if score >= 0.4 else "low",
            "confidence": "high" if angry_count + frustrated_count + pattern_count > 0 else "medium",
            "risk_tags": risk_tags,
        }

    def should_escalate(self, sentiment: str, consecutive_failures: int = 0) -> bool:
        """
        判断是否需要升级（转人工）。
        
        升级条件：
        - angry → 立即升级
        - frustrated + 连续 2 轮未解决 → 升级（避免用户持续受挫）
        - normal → 不升级
        """
        if sentiment == "angry":  # 愤怒立即转人工
            return True
        if sentiment == "frustrated" and consecutive_failures >= 2:  # 受挫且持续未解决
            return True
        return False  # 不需要升级


_detector = None  # 模块级单例缓存

def get_sentiment_detector() -> SentimentDetector:
    """
    获取全局 SentimentDetector 单例。
    无状态设计，单例仅用于避免重复创建对象。
    """
    global _detector  # 声明修改模块级变量
    if _detector is None:  # 首次调用时创建
        _detector = SentimentDetector()
    return _detector  # 返回单例
