"""
电商多智能体智能客服系统 - FastAPI 服务入口

核心架构：
1. 基于 LangGraph 的多智能体编排（Gateway -> Manager -> Domain Agents）
2. 全链路异步处理，支持高并发会话
3. 多级容错机制：缓存层 -> 重试层 -> 降级层
4. SSE 流式响应 + 情感分析转人工
5. Redis 持久化会话状态，支持分布式部署
"""

import asyncio  # 异步并发，用于超时控制和重试退避
import json  # JSON 序列化/反序列化
import logging  # 日志记录
import os  # 读取环境变量
import sys  # 路径操作
import time  # 时间戳，用于性能计时
from contextlib import asynccontextmanager  # 异步上下文管理器，用于应用生命周期管理

from fastapi import Depends, FastAPI, HTTPException, Request  # FastAPI 核心组件
from fastapi.responses import JSONResponse, Response  # JSON 响应封装
from fastapi.staticfiles import StaticFiles  # 静态文件挂载
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # LangChain 消息类型
from pydantic import BaseModel, Field  # 数据验证模型
from sse_starlette.sse import EventSourceResponse  # SSE 流式响应
import uvicorn  # ASGI 服务器

# 将项目根目录和 agents 目录加入模块搜索路径，方便导入本地模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../agents")))

from agents.gateway_agent import get_gateway_agent  # 网关智能体工厂函数
from agents.sentiment_model import get_sentiment_detector  # 情感检测器
from agents.stream_callback import format_sse, stream_agent_response  # SSE 格式化和流式回调
from service.refund_command import RefundCommandService
from service.ticket_store import TicketStore
from service.database import close_database, ensure_database_ready
from service.observability import configure_observability
from service.security import Principal, get_current_principal, require_roles
from service.settings import settings

# 全局变量，保存启动后的网关 agent 实例
gateway_agent = None

# 从环境变量读取超时时间和重试次数，默认值用于开发环境
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "45"))  # LLM 响应超时阈值（秒）
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "2"))  # 最大重试次数

# 日志基础配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 应用生命周期管理器。
    启动时初始化 gateway_agent，关闭时释放资源。
    使用 asynccontextmanager 而非旧版 startup/shutdown 事件，
    因为初始化和清理逻辑在同一作用域，代码更连贯且异常处理更好。
    """
    global gateway_agent
    settings.validate_for_startup()
    await ensure_database_ready()
    logging.info("[LIFESPAN] 开始初始化 gateway_agent")
    # 在应用启动时初始化 Agent 图，避免首个用户请求时冷启动延迟
    gateway_agent = await get_gateway_agent()
    logging.info("[LIFESPAN] gateway_agent 初始化完成")
    yield  # 控制权交还给 FastAPI，应用开始正常运行
    # 以下为应用关闭时的清理逻辑
    logging.info("[LIFESPAN] 开始释放资源")
    if gateway_agent is not None:
        try:
            # 使用 getattr 检查属性存在性，避免 AttributeError
            checkpoint = getattr(gateway_agent, "checkpointer", None)
            if checkpoint is not None and hasattr(checkpoint, "aclose"):
                await checkpoint.aclose()  # 关闭 Redis 连接池，防止连接泄漏
                logging.info("[LIFESPAN] Redis 连接已关闭")
        except Exception as exc:
            # 清理失败只记录警告，不影响进程退出
            logging.warning("[LIFESPAN] 关闭 Redis 连接失败: %s", exc)
    gateway_agent = None
    await close_database()
    logging.info("[LIFESPAN] 资源释放完成")


# 创建 FastAPI 应用实例，绑定生命周期管理器
app = FastAPI(
    title="电商多智能体智能客服平台",
    description="面向电商场景的多智能体智能客服 API（生产环境：AsyncRedisSaver）",
    version="1.0.0",
    lifespan=lifespan,
)
configure_observability(app)

# 挂载 html 静态目录，提供前端演示页面访问入口
html_directory = os.path.abspath(os.path.join(os.path.dirname(__file__), "../html"))
app.mount("/html", StaticFiles(directory=html_directory), name="html")


def _ensure_gateway_ready():
    """检查 gateway_agent 是否已初始化，未就绪则返回 503。"""
    if gateway_agent is None:
        raise HTTPException(status_code=503, detail="Agent is not initialized yet.")


def _get_cache():
    """
    延迟加载缓存模块，若不可用则返回 None。
    松耦合设计：缓存服务不可用时返回 None，主流程跳过缓存逻辑，实现优雅降级。
    """
    try:
        from agents.cache_layer import get_cache
        return get_cache()
    except Exception as exc:
        logging.warning("[API] 缓存模块不可用: %s", exc)
        return None


def _build_callbacks(session_id: str, tenant_id: str):
    """
    构建可选的回调列表，用于 Langfuse 追踪。
    将 session_id 设为 user_id，可追踪单个用户的完整对话链路。
    Langfuse 初始化失败时不影响主流程。
    """
    callbacks = []
    try:
        from agents.langfuse_integration import get_langfuse_handler
        langfuse_handler = get_langfuse_handler()
        if langfuse_handler is not None:
            langfuse_handler.session_id = session_id
            # 只保留运行定位信息，避免将原始对话和用户标识写入第三方追踪系统。
            langfuse_handler.metadata = {"tenant_id": tenant_id}
            callbacks.append(langfuse_handler)
    except Exception as exc:
        logging.warning("[API] Langfuse 初始化失败: %s", exc)
    return callbacks


async def _repair_incomplete_tool_call(config: dict):
    """
    修复上一次中断后残留的 tool call 状态（状态机自愈）。
    LangGraph 是基于状态机的，如果用户在工具调用过程中刷新页面或中断请求，
    状态可能停留在 tool_calling。此函数通过注入一个模拟的 ToolMessage 来闭环状态，
    让 Agent 能够自我恢复并继续对话，防止状态机卡死。
    """
    state = await gateway_agent.aget_state(config)
    if not state or "messages" not in state.values:
        return

    messages = state.values["messages"]
    if not messages:
        return

    last_message = messages[-1]
    # 检查最后一条消息是否是未完成的 AI Message（有 tool_calls 但无对应 ToolMessage）
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        tool_call = last_message.tool_calls[0]
        logging.warning("[API] 检测到未闭环的 tool call，准备修复: %s", tool_call.get("id"))
        # 注入一个假的 ToolMessage，告诉 LLM 工具执行失败，引导其进入下一轮对话
        fake_tool_output = ToolMessage(
            tool_call_id=tool_call["id"],
            content="Error: Tool execution failed or was interrupted. Please retry.",
        )
        await gateway_agent.aupdate_state(config, {"messages": [fake_tool_output]})


def _extract_ai_response(result: dict) -> str:
    """
    从 agent 返回结果中提取 AI 最终回复文本。
    同时兼容 dict 和对象两种格式，因为 LangChain 消息对象在不同版本或序列化后格式可能不同。
    """
    if not isinstance(result, dict) or "messages" not in result:
        raise HTTPException(status_code=500, detail="Agent 返回格式异常")

    messages = result.get("messages") or []
    if not messages:
        return ""

    last_message = messages[-1]
    if isinstance(last_message, dict):
        return last_message.get("content", "") or ""
    return getattr(last_message, "content", "") or ""


async def _invoke_agent_with_retries(user_message: str, config: dict) -> dict:
    """
    调用 gateway_agent 并支持超时与重试。
    - 超时控制：防止请求无限挂起耗尽 Worker 资源
    - 线性退避重试（0.5s, 1.0s）：给下游服务喘息时间，避免雪崩效应
    - 异常隔离：确保底层服务波动时 API 层仍能返回友好响应
    """
    user_msg = HumanMessage(content=user_message)
    last_error = None

    for attempt in range(1, AGENT_MAX_RETRIES + 1):
        try:
            logging.info("[API] 调用 agent.ainvoke，第 %s/%s 次尝试", attempt, AGENT_MAX_RETRIES)
            # asyncio.wait_for 为异步操作添加硬性超时
            return await asyncio.wait_for(
                gateway_agent.ainvoke({"messages": [user_msg]}, config=config),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            last_error = exc
            logging.warning("[API] 第 %s 次调用超时", attempt)
        except Exception as exc:
            last_error = exc
            logging.warning("[API] 第 %s 次调用失败: %s", attempt, exc, exc_info=True)

        # 线性退避：等待时间随重试次数递增，在快速恢复和减轻下游压力间取得平衡
        if attempt < AGENT_MAX_RETRIES:
            await asyncio.sleep(0.5 * attempt)

    raise last_error


def _session_config(principal: Principal) -> dict:
    session_id = f"{principal.tenant_id}:{principal.subject}"
    return {
        "configurable": {
            "thread_id": session_id,
            "user_id": principal.subject,
            "tenant_id": principal.tenant_id,
        }
    }


def _require_subject(path_user_id: str, principal: Principal) -> None:
    if path_user_id != principal.subject:
        raise HTTPException(status_code=403, detail="不允许访问其他用户资源")


async def _run_chat_request(principal: Principal, user_message: str) -> dict:
    """
    执行一次完整的对话请求，流程：
    1. 检查 gateway_agent 就绪状态
    2. 构建 Langfuse 回调（用于追踪）
    3. 修复可能残留的 tool call 状态
    4. 尝试从缓存获取结果（命中则直接返回）
    5. 调用智能体（带重试机制）
    6. 将结果存入缓存
    7. 返回结果（包含延迟、重试次数等元数据）
    """
    _ensure_gateway_ready()
    session_id = f"{principal.tenant_id}:{principal.subject}"
    callbacks = _build_callbacks(session_id, principal.tenant_id)
    # thread_id 是 LangGraph 的核心概念，用于隔离不同用户的会话上下文
    # 同一个 user_id 的多次请求共享同一份对话历史
    config = _session_config(principal)
    if callbacks:
        config["callbacks"] = callbacks

    # 每次请求前检查并修复可能残留的 tool call 状态
    await _repair_incomplete_tool_call(config)

    # 缓存查询：FAQ 场景下秒回，降低 LLM 调用成本
    cache = _get_cache()
    cached_reply = None
    if cache is not None:
        cached_reply = await cache.lookup(
            user_message,
            tenant_id=principal.tenant_id,
            knowledge_version=settings.knowledge_version,
        )
        if cached_reply:
            logging.info("[API] 缓存命中 session=%s", session_id)
            return {
                "response": cached_reply,
                "role": "assistant",
                "cached": True,
                "degraded": False,
                "retries": 0,
            }

    retries_used = 0
    degraded = False
    try:
        result = await _invoke_agent_with_retries(user_message, config)
        ai_response = _extract_ai_response(result)
        retries_used = max(AGENT_MAX_RETRIES - 1, 0)
    except asyncio.TimeoutError:
        # 超时降级：返回友好提示
        degraded = True
        ai_response = "当前请求处理超时，请稍后重试，或缩短问题范围后再次咨询。"
        logging.error("[API] 对话超时 session=%s", session_id)
    except Exception as exc:
        # 异常降级：返回错误信息并引导联系人工客服
        degraded = True
        retries_used = max(AGENT_MAX_RETRIES - 1, 0)
        logging.error("[API] 对话失败 session=%s", session_id, exc_info=True)
        ai_response = "抱歉，当前服务暂时不可用，请稍后再试。若问题紧急，请联系人工客服。"

    # 将成功产生的回复存入缓存，供后续相同问题使用
    if cache is not None and ai_response:
        await cache.store(
            query=user_message,
            reply=ai_response,
            transferred=False,
            tenant_id=principal.tenant_id,
            knowledge_version=settings.knowledge_version,
        )

    return {
        "response": ai_response,
        "role": "assistant",
        "cached": False,
        "degraded": degraded,
        "retries": retries_used,
    }


def _serialize_history_messages(messages):
    """
    序列化会话历史，过滤掉 SystemMessage 和 ToolMessage。
    - SystemMessage 是系统提示词，对用户无意义且可能泄露 prompt 细节
    - ToolMessage 是工具调用的原始 JSON 结果，不适合直接展示给用户
    只保留用户和助手的对话，符合聊天界面的展示需求。
    """
    history = []
    seen_ids = set()

    for msg in messages:
        msg_class = msg.__class__.__name__
        # 跳过系统消息和工具消息
        if msg_class in ["SystemMessage", "ToolMessage", "ToolMessageChunk"]:
            continue

        # 去重：LangGraph 在某些情况下（如重试时）可能会重复存储消息
        msg_id = getattr(msg, "id", None) or str(hash(str(getattr(msg, "content", ""))))
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)

        if msg_class == "HumanMessage":
            role = "user"
        elif msg_class in ["AIMessage", "AIMessageChunk"]:
            role = "assistant"
        else:
            logging.warning("[API] 未知消息类型: %s", msg_class)
            continue

        if hasattr(msg, "content"):
            history.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            history.append(msg)

    return history


def _parse_sse_payload(event: str):
    """解析 SSE 事件字符串，提取 event 类型和 JSON 数据。"""
    event_type = None
    data = None
    for line in event.splitlines():
        if line.startswith("event: "):
            event_type = line[len("event: ") :]
        elif line.startswith("data: "):
            try:
                data = json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                data = None
    return event_type, data


class TicketAssignRequest(BaseModel):
    """转接工单指派请求体"""
    agent_id: str = Field(min_length=1, max_length=128)


class TicketResolveRequest(BaseModel):
    """转接工单解决请求体"""
    agent_id: str = Field(min_length=1, max_length=128)
    resolution_note: str = Field(min_length=1, max_length=1000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=settings.chat_max_characters)


class RefundConfirmRequest(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    reason: str = Field(min_length=1, max_length=500)


@app.get("/")
async def root():
    """根路由，返回服务信息和文档入口"""
    return JSONResponse(content={"message": "电商多智能体智能客服平台 API", "docs": "/docs"})


@app.get("/api/health")
async def health_check():
    """就绪检查：仅在 Agent 已完成初始化时返回健康。"""
    if gateway_agent is None:
        raise HTTPException(status_code=503, detail="服务正在初始化")
    return JSONResponse(content={"status": "healthy"})


@app.get("/api/cache/stats")
async def get_cache_stats(_principal: Principal = Depends(require_roles("support", "admin"))):
    """获取缓存统计信息（命中率、大小等）"""
    cache = _get_cache()
    if cache is None:
        return JSONResponse(content={"enabled": False})
    return JSONResponse(content=await cache.stats())


@app.get("/metrics", include_in_schema=False)
async def metrics(_principal: Principal = Depends(require_roles("admin"))):
    """仅管理员可读取 Prometheus 指标，避免暴露业务流量细节。"""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/chat_history/{user_id}")
async def get_chat_history(user_id: str, principal: Principal = Depends(get_current_principal)):
    """获取指定用户的会话历史"""
    try:
        _ensure_gateway_ready()
        _require_subject(user_id, principal)
        config = _session_config(principal)
        state = await gateway_agent.aget_state(config)
        if not state or not state.values or "messages" not in state.values:
            return JSONResponse(content={"history": []})

        history = _serialize_history_messages(state.values["messages"])
        return JSONResponse(content={"history": history})
    except HTTPException:
        raise
    except Exception as exc:
        logging.error("[API] 获取对话历史失败", exc_info=True)
        raise HTTPException(status_code=500, detail="获取对话历史失败")


@app.post("/api/chat/{user_id}")
async def chat_with_agent(
    user_id: str,
    payload: ChatRequest,
    principal: Principal = Depends(get_current_principal),
):

    """普通对话接口，接收用户消息并返回 AI 回复"""
    try:
        request_started_at = time.time()
        _require_subject(user_id, principal)
        user_message = payload.message.strip()

        logging.info("[API] 收到对话请求 tenant=%s", principal.tenant_id)
        result = await _run_chat_request(principal=principal, user_message=user_message)
        # 记录请求延迟，用于性能监控和 SLA 保障
        result["latency_ms"] = int((time.time() - request_started_at) * 1000)
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception as exc:
        logging.error("[API] 对话接口异常", exc_info=True)
        raise HTTPException(status_code=500, detail="对话失败")


@app.post("/api/chat/{user_id}/stream")
async def chat_with_agent_stream(
    user_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):

    """
    流式对话接口，支持 SSE 实时返回和转人工处理。
    - 前置情感分析：在调用 LLM 之前检测用户情绪，节省 Token 并快速识别愤怒用户
    - SSE 流式推送：提升用户体验，减少等待焦虑
    - 缓存命中时也模拟打字机效果：保持体验一致性
    """

    try:
        _require_subject(user_id, principal)
        payload = ChatRequest.model_validate(await request.json())
        user_message = payload.message.strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="消息内容不能为空")

        _ensure_gateway_ready()
        config = _session_config(principal)
        await _repair_incomplete_tool_call(config)

        # 前置情感检测：轻量级模型拦截，避免不必要的 LLM 调用
        sentiment_detector = get_sentiment_detector()
        handoff_manager = TicketStore(tenant_id=principal.tenant_id)
        sentiment = sentiment_detector.detect(user_message)
        should_transfer, transfer_reason = await handoff_manager.should_transfer(
            user_id=principal.subject,
            message=user_message,
            sentiment=sentiment,
        )

        # 如果情绪负面或问题复杂，立即创建转接工单并返回 transfer 事件
        if should_transfer:
            ticket = await handoff_manager.create_transfer(
                user_id=principal.subject,
                reason=transfer_reason,
                summary=f"用户说: {user_message[:100]}",
                context={"sentiment": sentiment},
            )

            async def transfer_stream():
                yield format_sse(
                    "transfer",
                    {
                        "reason": transfer_reason,
                        "ticket_id": ticket["ticket_id"],
                        "message": "正在为您转接人工客服，请稍候。",
                    },
                )
                yield format_sse("done", {"total_tokens": 0, "cost": 0, "latency_ms": 0})

            return EventSourceResponse(transfer_stream())

        # 缓存命中时模拟打字机效果逐字返回
        cache = _get_cache()
        if cache is not None:
            cached_reply = await cache.lookup(
                user_message,
                tenant_id=principal.tenant_id,
                knowledge_version=settings.knowledge_version,
            )
            if cached_reply:
                logging.info("[API] 流式接口缓存命中 tenant=%s", principal.tenant_id)

                async def cached_stream():
                    yield format_sse("thinking", {"step": "找到相似问题的回答（缓存命中）"})
                    for char in cached_reply:
                        yield format_sse("token", {"content": char})
                        # 模拟打字速度，避免瞬间返回大量文本导致界面闪烁
                        await asyncio.sleep(0.01)
                    yield format_sse(
                        "done",
                        {"total_tokens": 0, "cost": 0, "latency_ms": 0, "cached": True},
                    )

                return EventSourceResponse(cached_stream())

        user_msg = HumanMessage(content=user_message)

        # 正常流式调用 Agent，实时推送 Token
        async def event_stream():
            collected_chunks = []
            has_error = False
            async for sse_event in stream_agent_response(gateway_agent, [user_msg], config):
                event_type, data = _parse_sse_payload(sse_event)
                if event_type == "token" and data:
                    collected_chunks.append(data.get("content", ""))
                elif event_type == "error":
                    has_error = True
                elif event_type == "done" and cache is not None and not has_error:
                    # 流式完成后将完整回复存入缓存
                    full_reply = "".join(collected_chunks).strip()
                    if full_reply:
                        await cache.store(
                            query=user_message,
                            reply=full_reply,
                            transferred=False,
                            tenant_id=principal.tenant_id,
                            knowledge_version=settings.knowledge_version,
                        )
                yield sse_event

        return EventSourceResponse(event_stream())
    except HTTPException:
        raise
    except Exception as exc:
        logging.error("[API] 流式对话异常", exc_info=True)
        raise HTTPException(status_code=500, detail="对话失败")


@app.post("/api/init_chat/{user_id}")
async def init_chat(user_id: str, principal: Principal = Depends(get_current_principal)):
    """
    初始化会话，如果已有状态则直接返回历史。
    用于给首次用户展示示例对话，降低使用门槛。
    具有幂等性：多次调用不会重复初始化。
    """
    try:
        _ensure_gateway_ready()
        _require_subject(user_id, principal)
        config = _session_config(principal)
        state = await gateway_agent.aget_state(config)

        if state and state.values and "messages" in state.values and len(state.values["messages"]) > 0:
            history = _serialize_history_messages(state.values["messages"])
            return JSONResponse(content={"history": history})

        initial_messages = [
            HumanMessage(content="我的订单怎么还没到？"),
            AIMessage(content="请告诉我你要查询的订单号。"),
        ]
        await gateway_agent.ainvoke({"messages": initial_messages}, config=config)
        return JSONResponse(
            content={
                "history": [
                    {"role": "user", "content": "我的订单怎么还没到？"},
                    {"role": "assistant", "content": "请告诉我你要查询的订单号。"},
                ]
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        logging.error("[API] 初始化对话失败", exc_info=True)
        raise HTTPException(status_code=500, detail="初始化对话失败")


@app.get("/api/feedback/{user_id}")
async def submit_feedback(
    user_id: str,
    score: int = None,
    principal: Principal = Depends(get_current_principal),
):
    """反馈接口，记录评分并返回当前 gateway prompt 版本信息，用于 A/B 测试效果对比"""
    _require_subject(user_id, principal)
    if score is not None:
        if score not in {1, 2, 3, 4, 5}:
            raise HTTPException(status_code=422, detail="评分范围必须为 1 到 5")
        from prompts.loader import get_prompt_loader

        loader = get_prompt_loader()
        version_info = loader.get_version_info("gateway", user_id)
        logging.info("[Feedback] tenant=%s score=%s version=%s", principal.tenant_id, score, version_info["version"])
        return JSONResponse(content={"status": "ok", "version": version_info["version"]})
    return JSONResponse(content={"status": "ignored"})


@app.get("/api/transfer/tickets")
async def list_transfer_tickets(
    status: str = None,
    limit: int = 50,
    principal: Principal = Depends(require_roles("support", "admin")),
):
    """列出转人工客服工单"""
    handoff = TicketStore(tenant_id=principal.tenant_id)
    tickets = await handoff.list_tickets(status=status, limit=max(1, min(limit, 100)))
    return JSONResponse(
        content={
            "pending_count": await handoff.get_pending_count(),
            "tickets": tickets,
        }
    )


@app.get("/api/transfer/tickets/{ticket_id}")
async def get_transfer_ticket(
    ticket_id: str,
    principal: Principal = Depends(require_roles("support", "admin")),
):
    """查询单个转接工单详情"""
    handoff = TicketStore(tenant_id=principal.tenant_id)
    ticket = await handoff.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Transfer ticket not found")
    return JSONResponse(content=ticket)


@app.post("/api/transfer/tickets/{ticket_id}/assign")
async def assign_transfer_ticket(
    ticket_id: str,
    request: TicketAssignRequest,
    principal: Principal = Depends(require_roles("support", "admin")),
):
    """指派人工客服处理工单"""
    if request.agent_id != principal.subject:
        raise HTTPException(status_code=403, detail="工单只能指派给当前认证客服")
    handoff = TicketStore(tenant_id=principal.tenant_id)
    try:
        ticket = await handoff.assign_ticket(ticket_id=ticket_id, agent_id=request.agent_id)
    except ValueError as exc:
        detail = str(exc)
        if detail == "ticket_not_found":
            raise HTTPException(status_code=404, detail="Transfer ticket not found")
        raise HTTPException(status_code=400, detail=detail)
    return JSONResponse(content=ticket)


@app.post("/api/transfer/tickets/{ticket_id}/resolve")
async def resolve_transfer_ticket(
    ticket_id: str,
    request: TicketResolveRequest,
    principal: Principal = Depends(require_roles("support", "admin")),
):
    """解决转接工单并记录处理结果"""
    if request.agent_id != principal.subject:
        raise HTTPException(status_code=403, detail="工单只能由当前认证客服处理")
    handoff = TicketStore(tenant_id=principal.tenant_id)
    try:
        ticket = await handoff.resolve_ticket(
            ticket_id=ticket_id,
            agent_id=request.agent_id,
            resolution_note=request.resolution_note,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "ticket_not_found":
            raise HTTPException(status_code=404, detail="Transfer ticket not found")
        raise HTTPException(status_code=400, detail=detail)
    return JSONResponse(content=ticket)


@app.get("/api/transfer/queue")
async def get_transfer_queue(principal: Principal = Depends(require_roles("support", "admin"))):
    """获取待处理转人工客服队列"""
    handoff = TicketStore(tenant_id=principal.tenant_id)
    return JSONResponse(
        content={
            "pending_count": await handoff.get_pending_count(),
            "tickets": await handoff.list_tickets(status="pending", limit=20),
        }
    )


@app.post("/api/orders/{order_no}/refund/confirm")
async def confirm_refund(
    order_no: str,
    request: RefundConfirmRequest,
    principal: Principal = Depends(get_current_principal),
):
    """仅在用户明确确认后执行退款申请，重复请求以幂等键安全返回。"""
    try:
        result = await RefundCommandService().confirm(
            tenant_id=principal.tenant_id,
            order_no=order_no,
            user_id=principal.subject,
            idempotency_key=request.idempotency_key,
            reason=request.reason,
        )
        logging.info("[Refund] tenant=%s order=%s status=%s", principal.tenant_id, order_no, result["status"])
        return JSONResponse(content=result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


if __name__ == "__main__":
    # reload=False 避免生产环境下的性能开销和不稳定
    # 多 worker 模式应使用：uvicorn service.main:app --workers 4
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
