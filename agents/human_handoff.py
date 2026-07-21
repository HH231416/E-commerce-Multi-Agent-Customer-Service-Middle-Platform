"""Human handoff workflow with SQLite persistence and optional Redis fan-out.

设计意图：
- 工单状态机：pending → assigned → resolved，完整生命周期管理
- SQLite 持久化：工单数据不丢失，服务重启后仍可查询
- Redis 队列（可选）：工单创建时推送到 Redis List，供人工客服系统消费
- 多触发条件：用户主动要求、情感检测愤怒、连续失败、AI 置信度低
- 为什么用 SQLite 而非 MySQL：工单量不大（中小电商），SQLite 零运维成本，
    单文件部署方便；如果需要分布式，可切换为 PostgreSQL
"""
import json  # JSON 序列化（存储 context）
import logging  # 日志记录
import os  # 读取环境变量
import sqlite3  # SQLite 数据库
import uuid  # 生成唯一工单 ID
from contextlib import closing  # 确保数据库连接自动关闭
from datetime import UTC, datetime  # UTC 时间戳
from typing import Any, Dict, List, Optional, Tuple  # 类型注解

try:
    import redis  # type: ignore  # Redis 客户端（可选依赖）
except ImportError:  # pragma: no cover - optional dependency
    redis = None  # Redis 不可用


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")  # Redis 连接地址
TRANSFER_QUEUE = "cs:transfer_queue"  # Redis 队列名称（转人工工单队列）
DEFAULT_DB_PATH = os.getenv(
    "HANDOFF_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "handoff_tickets.db")),
)  # 工单数据库路径


class HumanHandoffManager:
    """
    管理人工转接工单和基本工作流状态转换。
    
    状态机：
    pending ──assign──▶ assigned ──resolve──▶ resolved
    
    优先级：
    - high: 愤怒用户
    - medium: 受挫用户 / AI 置信度低
    - normal: 用户主动要求转人工
    """

    # 用户主动要求转人工的关键词
    TRANSFER_KEYWORDS = [
        "转人工",
        "找真人",
        "人工客服",
        "人工服务",
        "不是机器人",
        "活人",
        "真人",
        "不要AI",
    ]

    def __init__(
        self,
        redis_client: Any = ...,  # ... 是哨兵值，表示"使用默认 Redis 客户端"
        db_path: Optional[str] = None,  # 自定义数据库路径
        tenant_id: str = "default",
    ) -> None:
        self.db_path = os.path.abspath(db_path or DEFAULT_DB_PATH)  # 数据库文件路径
        self.tenant_id = tenant_id
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)  # 确保目录存在
        self._init_db()  # 初始化数据库表

        if redis_client is ...:  # 未传入自定义 Redis 客户端
            self.redis = self._build_redis_client()  # 自动构建
        else:
            self.redis = redis_client  # 使用传入的客户端

    def _build_redis_client(self):
        """
        构建 Redis 客户端（可选）。
        Redis 不可用时不影响核心功能（SQLite 仍可用）。
        """
        if redis is None:  # redis-py 未安装
            logging.info("[Handoff] redis dependency unavailable, using SQLite only.")
            return None

        try:
            client = redis.from_url(REDIS_URL)  # 创建连接
            client.ping()  # 测试连接
            return client
        except Exception as exc:  # pragma: no cover - depends on runtime infra
            logging.warning("[Handoff] Redis connection failed: %s", exc)
            return None  # 连接失败，Redis 不可用

    def _init_db(self) -> None:
        """
        初始化 SQLite 数据库和 handoff_tickets 表。
        使用 IF NOT EXISTS 保证幂等性。
        
        表字段说明：
        - ticket_id: 工单 ID（TK- 前缀 + UUID 前 10 位）
        - user_id: 发起转接的用户
        - reason: 转接原因（用户主动/情绪愤怒/AI 不足等）
        - summary: 问题摘要（用户消息前 100 字）
        - context_json: 上下文 JSON（情感状态等）
        - status: 工单状态（pending/assigned/resolved）
        - priority: 优先级（high/medium/normal）
        - assigned_to: 指派的客服 ID
        - resolution_note: 解决备注
        - source: 来源（chat/phone/email）
        - sentiment: 用户情感状态
        - created_at/updated_at/assigned_at/resolved_at: 时间戳
        """
        with closing(self._connect()) as conn:  # closing 确保连接自动关闭
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS handoff_tickets (
                    ticket_id TEXT PRIMARY KEY,  -- 工单唯一 ID
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    user_id TEXT NOT NULL,  -- 用户 ID
                    reason TEXT NOT NULL,  -- 转接原因
                    summary TEXT NOT NULL,  -- 问题摘要
                    context_json TEXT NOT NULL,  -- 上下文（JSON 格式）
                    status TEXT NOT NULL,  -- 工单状态
                    priority TEXT NOT NULL,  -- 优先级
                    assigned_to TEXT,  -- 指派人
                    resolution_note TEXT,  -- 解决备注
                    source TEXT,  -- 来源渠道
                    sentiment TEXT,  -- 情感状态
                    created_at TEXT NOT NULL,  -- 创建时间
                    updated_at TEXT NOT NULL,  -- 更新时间
                    assigned_at TEXT,  -- 指派时间
                    resolved_at TEXT  -- 解决时间
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(handoff_tickets)")}
            if "tenant_id" not in columns:
                conn.execute("ALTER TABLE handoff_tickets ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_handoff_tenant_status "
                "ON handoff_tickets(tenant_id, status, created_at)"
            )
            conn.commit()  # 提交 DDL

    def _connect(self) -> sqlite3.Connection:
        """创建数据库连接，设置 row_factory 为 Row（支持字典式访问）"""
        conn = sqlite3.connect(self.db_path)  # 创建连接
        conn.row_factory = sqlite3.Row  # 结果可通过列名访问
        return conn

    def _utcnow(self) -> str:
        """获取当前 UTC 时间字符串（ISO 8601 格式，Z 后缀）"""
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _serialize_ticket_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        """
        将数据库行序列化为字典。
        context_json 从 JSON 字符串反序列化为 Python 对象。
        """
        return {
            "ticket_id": row["ticket_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "reason": row["reason"],
            "summary": row["summary"],
            "context": json.loads(row["context_json"] or "{}"),  # JSON → dict
            "status": row["status"],
            "priority": row["priority"],
            "assigned_to": row["assigned_to"],
            "resolution_note": row["resolution_note"],
            "source": row["source"],
            "sentiment": row["sentiment"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "assigned_at": row["assigned_at"],
            "resolved_at": row["resolved_at"],
        }

    def _fetch_ticket_row(self, ticket_id: str) -> Optional[sqlite3.Row]:
        """按 ticket_id 查询工单原始行"""
        with closing(self._connect()) as conn:  # closing 确保连接关闭
            return conn.execute(
                "SELECT * FROM handoff_tickets WHERE ticket_id = ? AND tenant_id = ?",
                (ticket_id, self.tenant_id),
            ).fetchone()

    def _user_requests_transfer(self, message: str) -> bool:
        """检查用户消息中是否包含转人工关键词"""
        return any(keyword in message for keyword in self.TRANSFER_KEYWORDS)

    async def should_transfer(
        self,
        user_id: str,
        message: str,
        sentiment: str = "normal",  # 情感检测结果
        ai_confidence: str = "high",  # AI 置信度
        consecutive_failures: int = 0,  # 连续失败轮数
    ) -> Tuple[bool, str]:
        """
        判断是否需要转人工客服。
        
        输入：用户 ID、消息、情感、AI 置信度、连续失败次数
        输出：(是否需要转接, 转接原因)
        
        转接条件（按优先级）：
        1. 用户明确要求转人工
        2. 情感检测为 angry（愤怒）
        3. frustrated（受挫）+ 连续 2 轮未解决
        4. AI 置信度低
        """
        del user_id  # 当前未使用（保留给未来扩展，如基于用户历史判断）
        checks = [  # 按优先级排序的检查列表
            (self._user_requests_transfer(message), "用户明确要求转人工"),  # 最高优先级
            (sentiment == "angry", "检测到用户情绪愤怒"),  # 愤怒立即转
            (
                sentiment == "frustrated" and consecutive_failures >= 2,  # 受挫且持续未解决
                f"用户情绪受挫，且连续 {consecutive_failures} 轮未解决",
            ),
            (ai_confidence == "low", "AI 置信度不足"),  # AI 自己觉得回答不了
        ]
        for should, reason in checks:  # 按优先级检查
            if should:  # 满足条件
                return True, reason  # 返回需要转接和原因
        return False, ""  # 不需要转接

    async def create_transfer(
        self,
        user_id: str,
        reason: str,
        summary: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        创建转人工工单。
        
        输入：
        - user_id: 用户 ID
        - reason: 转接原因
        - summary: 问题摘要
        - context: 上下文信息（情感状态等）
        
        输出：创建的工单字典
        
        副作用：
        - 写入 SQLite
        - 推送到 Redis 队列（如果可用）
        """
        payload = context or {}  # 上下文，为空则用空字典
        now = self._utcnow()  # 当前时间
        ticket = {
            "ticket_id": f"TK-{uuid.uuid4().hex[:10].upper()}",  # 生成工单 ID：TK-XXXXXXXXXX
            "tenant_id": self.tenant_id,
            "user_id": user_id,
            "reason": reason,
            "summary": summary,
            "context": payload,
            "status": "pending",  # 初始状态：待处理
            "priority": self._infer_priority(reason=reason, sentiment=payload.get("sentiment")),  # 自动推断优先级
            "assigned_to": None,  # 尚未指派
            "resolution_note": None,  # 无解决备注
            "source": payload.get("source", "chat"),  # 来源：默认聊天
            "sentiment": payload.get("sentiment", "normal"),  # 情感状态
            "created_at": now,  # 创建时间
            "updated_at": now,  # 更新时间
            "assigned_at": None,  # 尚未指派
            "resolved_at": None,  # 尚未解决
        }

        # 写入 SQLite
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO handoff_tickets (
                    ticket_id, tenant_id, user_id, reason, summary, context_json, status, priority,
                    assigned_to, resolution_note, source, sentiment,
                    created_at, updated_at, assigned_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,  # 参数化插入
                (
                    ticket["ticket_id"],
                    ticket["tenant_id"],
                    ticket["user_id"],
                    ticket["reason"],
                    ticket["summary"],
                    json.dumps(ticket["context"], ensure_ascii=False),  # 上下文序列化为 JSON
                    ticket["status"],
                    ticket["priority"],
                    ticket["assigned_to"],
                    ticket["resolution_note"],
                    ticket["source"],
                    ticket["sentiment"],
                    ticket["created_at"],
                    ticket["updated_at"],
                    ticket["assigned_at"],
                    ticket["resolved_at"],
                ),
            )
            conn.commit()  # 提交事务

        # 推送到 Redis 队列（供人工客服系统消费）
        if self.redis is not None:  # Redis 可用
            try:  # pragma: no cover - depends on runtime infra
                self.redis.rpush(  # 从右侧推入队列（FIFO）
                    TRANSFER_QUEUE,
                    json.dumps(
                        {
                            "ticket_id": ticket["ticket_id"],
                            "tenant_id": ticket["tenant_id"],
                            "user_id": ticket["user_id"],
                            "reason": ticket["reason"],
                            "priority": ticket["priority"],
                        },
                        ensure_ascii=False,  # 保留中文
                    ),
                )
            except Exception as exc:  # Redis 推送失败不影响核心流程
                logging.warning("[Handoff] Failed to publish ticket to Redis queue: %s", exc)

        logging.info("[Handoff] Created transfer ticket %s", ticket["ticket_id"])
        return ticket

    def _infer_priority(self, reason: str, sentiment: Optional[str]) -> str:
        """
        根据转接原因和情感状态推断优先级。
        
        优先级规则：
        - angry 或原因含"愤怒" → high（需要立即处理）
        - frustrated 或置信度不足 → medium
        - 其他 → normal
        """
        if sentiment == "angry" or "愤怒" in reason:  # 愤怒用户最高优先级
            return "high"
        if sentiment == "frustrated" or "置信度不足" in reason:  # 受挫/AI 不足中等优先级
            return "medium"
        return "normal"  # 默认

    def get_pending_count(self) -> int:
        """获取待处理工单数量（供客服看板使用）"""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM handoff_tickets WHERE tenant_id = ? AND status = 'pending'",
                (self.tenant_id,),
            ).fetchone()
        return int(row["total"] if row else 0)  # 安全取值

    def list_tickets(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        列出工单，支持按状态筛选。
        
        输入：status（可选筛选）、limit（最大返回数）
        输出：工单字典列表（按创建时间倒序）
        """
        query = "SELECT * FROM handoff_tickets WHERE tenant_id = ?"
        params: List[Any] = [self.tenant_id]
        if status:  # 按状态筛选
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"  # 最新优先
        params.append(limit)

        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._serialize_ticket_row(row) for row in rows]  # 序列化每一行

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """查询单个工单详情"""
        row = self._fetch_ticket_row(ticket_id)  # 获取原始行
        if row is None:  # 未找到
            return None
        return self._serialize_ticket_row(row)  # 序列化返回

    def assign_ticket(self, ticket_id: str, agent_id: str) -> Dict[str, Any]:
        """
        指派工单给人工客服。
        
        状态转换：pending → assigned
        
        输入：工单 ID、客服 ID
        输出：更新后的工单
        异常：工单不存在（ValueError: ticket_not_found）、已解决（ticket_already_resolved）
        """
        ticket = self.get_ticket(ticket_id)  # 先查询工单
        if ticket is None:  # 不存在
            raise ValueError("ticket_not_found")
        if ticket["status"] == "resolved":  # 已解决，不能再指派
            raise ValueError("ticket_already_resolved")

        now = self._utcnow()  # 当前时间
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE handoff_tickets
                SET status = ?, assigned_to = ?, assigned_at = ?, updated_at = ?
                WHERE ticket_id = ? AND tenant_id = ?
                """,  # 更新状态、指派人、指派时间、更新时间
                ("assigned", agent_id, now, now, ticket_id, self.tenant_id),
            )
            conn.commit()
        return self.get_ticket(ticket_id)  # 返回更新后的工单

    def resolve_ticket(self, ticket_id: str, agent_id: str, resolution_note: str) -> Dict[str, Any]:
        """
        解决工单。
        
        状态转换：assigned → resolved
        
        输入：工单 ID、客服 ID、解决备注
        输出：更新后的工单
        异常：工单不存在（ValueError: ticket_not_found）
        """
        ticket = self.get_ticket(ticket_id)
        if ticket is None:  # 不存在
            raise ValueError("ticket_not_found")

        now = self._utcnow()  # 当前时间
        assigned_to = ticket["assigned_to"] or agent_id  # 保留原有指派人
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE handoff_tickets
                SET status = ?, assigned_to = ?, resolution_note = ?, resolved_at = ?, updated_at = ?
                WHERE ticket_id = ? AND tenant_id = ?
                """,  # 更新为已解决状态
                ("resolved", assigned_to, resolution_note, now, now, ticket_id, self.tenant_id),
            )
            conn.commit()
        return self.get_ticket(ticket_id)  # 返回更新后的工单
