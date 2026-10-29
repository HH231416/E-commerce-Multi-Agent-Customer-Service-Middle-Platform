"""Redis 语义缓存：命中后跳过 LLM 调用，并提供运行时统计。

设计意图：
- 语义缓存而非精确匹配：用户问"怎么退款"和"如何退货"语义相似，精确匹配无法命中
- 向量相似度：用 Embedding 计算 query 相似度，threshold=0.92 过滤不相关查询
- 差异化 TTL：政策缓存 24 小时（稳定不变），商品静态信息 30 分钟，默认类 10 分钟；订单/物流等动态类不缓存（见下条"为什么订单不缓存"）
- 为什么订单不缓存：订单状态实时变化（配送中→已签收），缓存会导致返回过期信息
- 为什么缓存键不带租户：当前为单租户系统，多租户场景需在 cache_key 中加入 tenant_id
"""
import hashlib  # MD5 哈希，用于生成缓存键
import logging  # 日志记录
import os  # 读取环境变量
import time  # 时间戳
from typing import Optional  # 类型注解

try:
    import redis  # Redis Python 客户端
except ImportError:  # pragma: no cover - 依赖缺失时优雅降级
    redis = None  # Redis 不可用


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")  # Redis 连接地址

# 不同类别查询的缓存 TTL（秒）
TTL_CONFIG = {
    "order": 300,  # 订单相关：5 分钟（状态可能变化）
    "product": 1800,  # 商品相关：30 分钟（价格库存变化较慢）
    "policy": 86400,  # 政策相关：24 小时（售后政策基本不变）
    "default": 600,  # 默认：10 分钟
}

# 回复中包含以下关键词时不缓存（状态实时变化，缓存会返回过期信息）
REALTIME_KEYWORDS = [
    "退款中",  # 退款状态实时变化
    "配送中",  # 物流位置实时变化
    "已签收",  # 签收后状态变化
    "待发货",  # 发货状态变化
    "已发货",  # 同上
    "处理中",  # 处理进度变化
    "审核中",  # 审核进度变化
    "拦截配送",  # 物流拦截结果实时
]

# 回复中包含以下关键词时不缓存（错误/降级/转人工回复不应缓存）
NON_CACHEABLE_REPLY_KEYWORDS = [
    "error:",  # 错误信息
    "抱歉，暂时无法",  # 服务不可用
    "请稍后再试",  # 临时故障
    "知识库暂时不可用",  # RAG 降级
    "转接人工客服",  # 转人工
    "转人工",  # 转人工
]

_cache_instance = None  # 全局缓存单例


def _record_cache_lookup(result: str) -> None:
    """把缓存命中情况计入 Prometheus；指标模块不可用时静默跳过，不影响主链路。"""
    try:
        from service.observability import CACHE_LOOKUPS  # 延迟导入，避免 agents 反向依赖服务层

        CACHE_LOOKUPS.labels(result).inc()
    except Exception:
        pass


class SemanticCache:
    """
    基于 Redis 的向量语义缓存。
    
    工作原理：
    1. 存储时将 query 向量化，以 Hash 结构存入 Redis
    2. 查询时将用户 query 向量化，遍历缓存计算余弦相似度
    3. 相似度 >= threshold 且最高分者命中
    4. 命中直接返回缓存回复，跳过 LLM 调用
    
    局限性：
    - 遍历所有缓存键（O(n)），缓存量大时需要改用 Redis Vector Search
    - 单机部署，未使用 Redis Cluster
    """

    def __init__(self, similarity_threshold: float = 0.92):
        self.threshold = similarity_threshold  # 相似度阈值（0.92 通过实验调优，平衡命中率和准确率）
        self.embeddings = None  # Embedding 模型（懒加载）
        self.redis = None  # Redis 客户端
        # 运行时统计计数器
        self.hits = 0  # 缓存命中次数
        self.misses = 0  # 缓存未命中次数
        self.stores = 0  # 成功写入次数
        self.store_skips = 0  # 跳过写入次数（不符合缓存条件）
        self.lookup_errors = 0  # 查询错误次数
        self.store_errors = 0  # 写入错误次数
        self._init_redis()  # 初始化 Redis 连接

    def _init_redis(self):
        """
        初始化 Redis 连接。
        失败时不阻塞服务，缓存功能静默降级为关闭状态。
        """
        if redis is None:  # redis-py 未安装
            logging.warning("[Cache] redis 依赖未安装，缓存功能降级为关闭状态")
            self.redis = None  # 显式设为 None
            return

        try:
            self.redis = redis.from_url(REDIS_URL)  # 创建 Redis 连接
            self.redis.ping()  # 测试连接（快速失败）
            logging.info("[Cache] Redis 连接成功")
        except Exception as exc:
            logging.warning("[Cache] Redis 连接失败: %s，缓存功能不可用", exc)
            self.redis = None  # 连接失败，缓存不可用

    async def _get_embeddings(self):
        """
        延迟加载 Embedding 模型。
        首次 lookup 或 store 时调用，避免 import cache_layer 时就加载模型。
        """
        if self.embeddings is None:  # 尚未加载
            try:
                from agents.model import get_embeddings  # 优先从 agents 包导入
            except ImportError:  # pragma: no cover - 兼容直接脚本运行
                from model import get_embeddings  # 回退到直接导入
            self.embeddings = get_embeddings()  # 获取 Embedding 模型实例
        return self.embeddings

    def _classify_query(self, query: str) -> str:
        """
        根据关键词将查询分类，用于确定缓存 TTL。
        
        分类策略（按优先级）：
        1. order: 包含订单/物流/退款等关键词 → 5 分钟 TTL
        2. product: 包含商品/价格/库存等关键词 → 30 分钟 TTL
        3. policy: 包含政策/规则/售后等关键词 → 24 小时 TTL
        4. default: 其他 → 10 分钟 TTL
        """
        order_keywords = ["订单", "物流", "发货", "退款", "配送", "签收", "快递", "取消", "退货", "ORD"]
        product_keywords = ["商品", "价格", "多少钱", "库存", "有货", "参数", "规格", "品牌", "型号"]
        policy_keywords = ["政策", "规则", "售后", "无理由", "保修", "退货条件", "退款时效", "手续费"]

        for keyword in order_keywords:  # 检查订单关键词
            if keyword in query:
                return "order"
        for keyword in product_keywords:  # 检查商品关键词
            if keyword in query:
                return "product"
        for keyword in policy_keywords:  # 检查政策关键词
            if keyword in query:
                return "policy"
        return "default"  # 无法分类

    def _contains_realtime_status(self, reply: str) -> bool:
        """检查回复中是否包含实时状态关键词（如"配送中"），是则不应缓存"""
        return any(keyword in reply for keyword in REALTIME_KEYWORDS)

    def should_cache_reply(
        self,
        query: str,
        reply: str,
        transferred: bool = False,
        scope: str = "public",
    ) -> bool:
        """
        判断回复是否应该被缓存。
        
        不应缓存的情况：
        1. 空查询或空回复
        2. 转人工的回复
        3. 包含实时状态（如"配送中"）
        4. 包含错误/降级关键词
        """
        normalized_reply = (reply or "").strip()  # 标准化回复文本
        if not query.strip() or not normalized_reply:  # 空内容不缓存
            return False
        if transferred or scope != "public":
            return False
        # 订单、物流、退款结果始终可能包含个人数据或实时状态，不允许缓存。
        if self._classify_query(query) == "order":
            return False
        if self._contains_realtime_status(normalized_reply):  # 实时状态不缓存
            return False

        lowered = normalized_reply.lower()  # 大小写不敏感
        return not any(keyword in lowered for keyword in NON_CACHEABLE_REPLY_KEYWORDS)  # 无禁止关键词

    async def lookup(
        self,
        query: str,
        *,
        tenant_id: str = "default",
        knowledge_version: str = "unversioned",
        scope: str = "public",
    ) -> Optional[str]:
        """
        查询缓存：将用户 query 向量化后与缓存中的向量计算余弦相似度。
        
        输入：用户查询字符串
        输出：缓存的回复文本（命中）或 None（未命中）
        失败处理：异常时记录错误计数并返回 None（不中断主流程）
        
        性能注意：当前实现遍历所有缓存键（O(n)），生产环境建议改用 Redis Vector Search (RediSearch)
        """
        if self.redis is None or scope != "public" or self._classify_query(query) == "order":
            self.misses += 1  # 计为未命中
            _record_cache_lookup("miss")
            return None

        try:
            import numpy as np  # 向量计算只在实际缓存检索时加载

            embeddings = await self._get_embeddings()  # 获取 Embedding 模型
            query_vector = await embeddings.aembed_query(query)  # 将查询文本转为向量
            query_vec = np.array(query_vector, dtype=np.float32)  # 转为 numpy 数组

            best_score = 0.0  # 最高相似度分数
            best_reply = None  # 最高分对应的回复
            namespace = f"cs:cache:{tenant_id}:{knowledge_version}:{scope}:*"
            # SCAN 不会阻塞 Redis；大规模部署应以 RediSearch 向量索引替换本回退实现。
            for key in self.redis.scan_iter(match=namespace, count=100):
                cached_data = self.redis.hgetall(key)  # 获取 Hash 中所有字段
                if b"vector" not in cached_data or b"reply" not in cached_data:  # 数据不完整，跳过
                    continue

                cached_vector = np.frombuffer(cached_data[b"vector"], dtype=np.float32)  # 反序列化向量
                # 计算余弦相似度：cos(θ) = (A·B) / (|A| × |B|)
                denominator = np.linalg.norm(query_vec) * np.linalg.norm(cached_vector)  # |A| × |B|
                if denominator == 0:  # 零向量（异常情况）
                    continue

                score = float(np.dot(query_vec, cached_vector) / denominator)  # 余弦相似度
                if score >= self.threshold and score > best_score:  # 超过阈值且是当前最高分
                    best_score = score  # 更新最高分
                    best_reply = cached_data[b"reply"].decode("utf-8")  # 解码回复文本

            if best_reply:  # 命中缓存
                self.hits += 1  # 命中计数
                _record_cache_lookup("hit")
                logging.info("[Cache] 命中 score=%.3f query='%s...'", best_score, query[:30])
                return best_reply

            self.misses += 1  # 未命中计数
            _record_cache_lookup("miss")
            return None
        except Exception as exc:  # 查询异常
            self.lookup_errors += 1  # 错误计数
            self.misses += 1  # 也算未命中
            _record_cache_lookup("error")
            logging.warning("[Cache] 查询失败: %s", exc)
            return None  # 返回 None，不影响主流程

    async def store(
        self,
        query: str,
        reply: str,
        transferred: bool = False,
        *,
        tenant_id: str = "default",
        knowledge_version: str = "unversioned",
        scope: str = "public",
    ) -> bool:
        """
        将查询和回复存入缓存。
        
        输入：query（用户问题）、reply（AI 回复）、transferred（是否转人工）
        输出：True（写入成功）或 False（跳过/失败）
        失败处理：写入失败不影响主流程（仅记录日志和计数）
        
        存储结构（Redis Hash）：
        - vector: 查询向量（float32 二进制）
        - reply: 回复文本
        - query: 原始查询（截断到 200 字符）
        - timestamp: 写入时间戳
        - category: 查询分类（用于 TTL）
        """
        if not self.should_cache_reply(
            query=query, reply=reply, transferred=transferred, scope=scope
        ):
            self.store_skips += 1  # 跳过计数
            return False

        if self.redis is None:  # Redis 不可用
            self.store_skips += 1  # 跳过计数
            return False

        try:
            import numpy as np  # 向量计算只在实际缓存写入时加载

            embeddings = await self._get_embeddings()  # 获取 Embedding 模型
            query_vector = await embeddings.aembed_query(query)  # 查询向量化
            query_vec_bytes = np.array(query_vector, dtype=np.float32).tobytes()  # 向量转二进制
            # 缓存键包含租户、知识版本和可见范围，避免跨租户或知识版本串答。
            digest = hashlib.sha256(query_vec_bytes).hexdigest()[:32]
            cache_key = f"cs:cache:{tenant_id}:{knowledge_version}:{scope}:{digest}"
            category = self._classify_query(query)  # 查询分类
            ttl = TTL_CONFIG.get(category, TTL_CONFIG["default"])  # 根据分类获取 TTL

            # 使用 hset 存储多个字段
            self.redis.hset(
                cache_key,
                mapping={
                    "vector": query_vec_bytes,  # 向量（二进制）
                    "reply": reply,  # 回复文本
                    "query": query[:200],  # 原始查询（截断防止过大）
                    "timestamp": str(time.time()),  # 写入时间戳
                    "category": category,  # 分类标签
                    "tenant_id": tenant_id,
                    "knowledge_version": knowledge_version,
                    "scope": scope,
                },
            )
            self.redis.expire(cache_key, ttl)  # 设置过期时间
            self.stores += 1  # 写入计数
            logging.info("[Cache] 已写入 category=%s ttl=%ss", category, ttl)
            return True
        except Exception as exc:  # 写入异常
            self.store_errors += 1  # 错误计数
            logging.warning("[Cache] 写入失败: %s", exc)
            return False

    async def stats(self) -> dict:
        """
        获取缓存运行时统计信息。
        用于 /api/cache/stats 接口，监控缓存效果。
        
        返回字段：
        - enabled: 缓存是否可用
        - total_entries: 当前缓存条目数
        - threshold: 相似度阈值
        - hits/misses: 命中/未命中次数
        - stores/store_skips: 写入/跳过次数
        - lookup_errors/store_errors: 错误次数
        """
        total_entries = 0  # 当前缓存条目数
        enabled = self.redis is not None  # 缓存是否启用
        if enabled:
            try:
                total_entries = sum(1 for _ in self.redis.scan_iter(match="cs:cache:*", count=100))
            except Exception as exc:
                enabled = False  # 查询失败，标记为不可用
                self.lookup_errors += 1  # 错误计数
                logging.warning("[Cache] 统计失败: %s", exc)

        return {
            "enabled": enabled,
            "total_entries": total_entries,
            "threshold": self.threshold,
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "store_skips": self.store_skips,
            "lookup_errors": self.lookup_errors,
            "store_errors": self.store_errors,
        }


def get_cache() -> SemanticCache:
    """
    获取全局 SemanticCache 单例。
    在 main.py 的 _get_cache() 中延迟调用，不影响应用启动。
    """
    global _cache_instance  # 声明修改模块级变量
    if _cache_instance is None:  # 首次调用时创建
        _cache_instance = SemanticCache()
    return _cache_instance  # 返回单例
