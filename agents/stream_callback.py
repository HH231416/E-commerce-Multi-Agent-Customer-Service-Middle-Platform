"""SSE 流式事件回调：捕获 Agent 执行过程中的思考步骤和 Token 输出

设计意图：
- 实时反馈：用户不需要等待完整回复，可以逐字看到 AI 生成的内容
- 透明度：展示"正在查询订单""正在搜索商品"等步骤，让用户知道系统在做什么
- 回退机制：astream_events 在部分 Agent 配置下可能不产出事件，此时降级到 ainvoke
- 为什么不用 WebSocket：SSE 更简单（单向推送），HTTP 兼容性更好，
    客服场景不需要双向实时通信（用户发送消息用普通 POST 即可）
"""
import json  # JSON 序列化
import time  # 时间戳，计算延迟
from typing import AsyncGenerator  # 异步生成器类型注解


class StreamEventType:
    """SSE 事件类型常量"""
    THINKING = "thinking"      # 思考状态（如：正在查询订单...）
    TOOL_START = "tool_start"  # 工具开始调用
    TOOL_END = "tool_end"      # 工具调用结束
    RAG_RETRIEVAL = "rag_retrieval"  # RAG 检索事件（当前未使用，预留）
    TOKEN = "token"            # LLM 生成的文本片段（逐字推送）
    TRANSFER = "transfer"      # 智能体转接（转人工）
    ERROR = "error"            # 错误信息
    DONE = "done"              # 任务完成（包含 Token 用量和延迟）


def format_sse(event_type: str, data: dict) -> str:
    """
    将事件格式化为 SSE 协议字符串。
    
    SSE 格式：event: <type>\ndata: <json>\n\n
    ensure_ascii=True：中文使用 Unicode 转义编码（部分 SSE 客户端要求 ASCII 安全）
    """
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=True)}\n\n"


async def stream_agent_response(
    agent,  # LangGraph CompiledGraph 实例
    messages: list,  # 消息列表（包含用户消息）
    config: dict,  # LangGraph 运行时配置
) -> AsyncGenerator[str, None]:
    """
    流式执行 Agent 并产出 SSE 事件。
    使用 LangGraph 的 astream_events API 捕获 on_chat_model_stream、on_tool_start、on_tool_end 等事件。
    
    输入：Agent 实例、消息列表、配置
    输出：SSE 事件字符串的异步生成器
    失败处理：
    - astream_events 无产出 → 回退到 ainvoke（非流式）
    - 整体异常 → 发送 ERROR 事件
    """
    start_time = time.time()  # 记录开始时间（用于计算延迟）
    total_tokens = 0  # 累计 Token 用量
    current_thinking_step = None  # 当前思考步骤描述

    has_content = False  # 标记是否产出了任何内容（用于回退判断）

    try:
        # 初始思考事件：告诉用户系统正在分析
        yield format_sse(StreamEventType.THINKING, {
            "step": "开始分析您的问题..."
        })

        # 使用 LangGraph v2 事件 API 流式监听
        async for event in agent.astream_events(
            {"messages": messages},  # 输入消息
            config=config,  # 运行时配置（包含 thread_id）
            version="v2",  # 使用 v2 事件格式
        ):
            event_name = event.get("event", "")  # 事件名称
            event_data = event.get("data", {})  # 事件数据

            # Token 级别流式输出（LLM 每生成一个 token 触发一次）
            if event_name == "on_chat_model_stream":
                chunk = event_data.get("chunk", {})  # 获取 chunk 对象
                if hasattr(chunk, "content") and chunk.content:  # 有文本内容
                    has_content = True  # 标记已产出内容
                    yield format_sse(StreamEventType.TOKEN, {
                        "content": chunk.content  # 推送单个 token
                    })
                if hasattr(chunk, "response_metadata"):  # 尝试提取 Token 用量
                    usage = chunk.response_metadata.get("token_usage", {})
                    total_tokens = usage.get("total_tokens", total_tokens)

            # 工具调用开始（Agent 决定调用某个 MCP 工具）
            elif event_name == "on_tool_start":
                has_content = True  # 标记已产出内容
                tool_name = event.get("name", "unknown")  # 工具名称
                tool_input = event_data.get("input", {})  # 工具输入参数
                # 过滤敏感参数（user_id 由系统注入，不应暴露给前端）
                safe_input = {k: v for k, v in tool_input.items()
                              if k not in ("user_id", "config")}

                yield format_sse(StreamEventType.TOOL_START, {
                    "tool": tool_name,
                    "args": safe_input,  # 安全的参数（已过滤敏感信息）
                })

                # 工具名 → 友好的中文提示映射
                thinking_map = {
                    "get_order": f"正在查询订单 {safe_input.get('order_no', '')}...",
                    "check_cancelable": "正在检查订单是否可以取消...",
                    "create_refund_draft": "正在核验退款条件...",
                    "get_product_info": "正在查询商品信息...",
                    "search_products": "正在搜索商品...",
                    "get_policy": "正在检索相关政策...",
                    "query_knowledge_base": "正在从知识库检索信息...",
                }
                step_msg = thinking_map.get(tool_name, f"正在调用 {tool_name}...")  # 未知工具用通用提示
                current_thinking_step = step_msg  # 记录当前步骤
                yield format_sse(StreamEventType.THINKING, {"step": step_msg})

            # 工具调用结束
            elif event_name == "on_tool_end":
                has_content = True  # 标记已产出内容
                yield format_sse(StreamEventType.TOOL_END, {
                    "tool": event.get("name", "unknown"),
                    "summary": "数据已获取，正在整理回复..."  # 告诉用户数据拿到了
                })

            # LLM 开始生成回复（工具调用完成后）
            elif event_name == "on_chat_model_start":
                if current_thinking_step:  # 之前有工具调用
                    yield format_sse(StreamEventType.THINKING, {
                        "step": "正在组织回复..."  # 告诉用户正在组织语言
                    })

        # === 回退逻辑：astream_events 没有产生任何内容 ===
        # 原因：某些 Agent 配置（如旧版 LangGraph）可能不支持 astream_events
        if not has_content:  # 流式没有产出任何内容
            print("[Stream] astream_events 未产生内容，回退到 ainvoke")
            result = await agent.ainvoke(  # 降级为非流式调用
                {"messages": messages},
                config=config,
            )
            if isinstance(result, dict) and "messages" in result:  # 正常返回
                last_msg = result["messages"][-1]  # 取最后一条消息
                content = getattr(last_msg, "content", "") or ""  # 提取文本
                if content:
                    for char in content:  # 逐字符推送（模拟流式效果）
                        yield format_sse(StreamEventType.TOKEN, {"content": char})
                total_tokens = len(content)  # 估算 Token 数（非精确）

        # 发送完成事件
        elapsed_ms = int((time.time() - start_time) * 1000)  # 计算总延迟
        estimated_cost = round(total_tokens * 0.000004, 4)  # 估算费用（按 ¥0.004/1K tokens）

        yield format_sse(StreamEventType.DONE, {
            "total_tokens": total_tokens,  # Token 用量
            "cost": estimated_cost,  # 估算费用
            "latency_ms": elapsed_ms,  # 总延迟
        })

    except Exception as e:  # 整体异常捕获
        yield format_sse(StreamEventType.ERROR, {
            "message": f"处理出错: {str(e)}"  # 错误信息（生产环境应脱敏）
        })
