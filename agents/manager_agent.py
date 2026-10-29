# Manager Agent — 总控协调器 (v3: 自定义 StateGraph 并行编排)
#
# 架构演进：
#   v1: create_deep_agent 黑盒，LLM 自行决定子 Agent 调用顺序（串行）
#   v2: 定义 _run_sub_agents_parallel 但未接入主链路
#   v3: 自定义 LangGraph StateGraph，Plan → Execute → Synthesize 三阶段流水线，
#       Execute 节点接入 _run_sub_agents_parallel，实现真正的单轮多意图并行执行
#
# 架构定位：
# - 输入：Gateway Agent 转发的用户消息（标准 LangGraph 消息格式）
# - 输出：协调子 Agent 后汇总的最终回复
# - 失败处理：子 Agent 异常被 asyncio.gather 的 return_exceptions 捕获，不阻塞其他 Agent
# - 为什么用并行编排而非串行：用户可能同时问"我的订单到哪了，有什么优惠券"，串行需要两轮对话，
#     并行可以在一次请求中同时查询物流和优惠券，减少 50% 以上的响应时间
import asyncio
import logging
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from model import get_model, get_routing_model
from RAG_tool import query_knowledge_base

# ---------------------------------------------------------------------------
# State 定义
# ---------------------------------------------------------------------------


class SubTask(BaseModel):
    """单个子任务：由 LLM Plan 节点输出的结构化任务描述。"""
    agent: str = Field(description="目标 Agent: order | product | logistics | coupon | policy")
    query: str = Field(description="针对该 Agent 重构后的子查询")


class IntentPlan(BaseModel):
    """LLM 意图识别与任务规划的结构化输出。"""
    is_chat: bool = Field(default=False, description="是否为简单闲聊/问候")
    chat_reply: str = Field(default="", description="闲聊时的直接回复")
    tasks: list[SubTask] = Field(default_factory=list, description="需要并行执行的子任务列表")


class ManagerState(TypedDict):
    """Manager Agent 内部状态。plan 和 agent_outputs 为单轮临时字段，不跨轮次持久化。"""
    messages: Annotated[list[BaseMessage], add_messages]
    plan: list[dict]           # [{agent, query}, ...]
    agent_outputs: list[dict]  # [{agent, content, error}, ...]


# ---------------------------------------------------------------------------
# Agent 注册表（懒加载）
# ---------------------------------------------------------------------------
_agents: dict[str, object] = {}


async def _ensure_agents() -> dict:
    """延迟加载 4 个领域 Agent，首次调用时初始化，后续复用。"""
    if not _agents:
        from coupon_agent import get_coupon_agent
        from logistics_agent import get_logistics_agent
        from order_agent import get_order_agent
        from product_agent import get_product_agent

        _agents["order"] = await get_order_agent()
        _agents["product"] = await get_product_agent()
        _agents["logistics"] = await get_logistics_agent()
        _agents["coupon"] = await get_coupon_agent()
        logging.info("[Manager] 4 个领域 Agent 已就绪")
    return _agents


# ---------------------------------------------------------------------------
# Node 1: Plan — LLM 结构化意图识别与任务规划
# ---------------------------------------------------------------------------

PLAN_PROMPT = """你是一个电商客服调度员。分析用户消息，判断涉及哪些业务领域，并为每个领域创建子查询。

可用领域 Agent：
- "order": 订单状态、可取消检查、退款资格
- "product": 商品信息、价格、搜索、推荐
- "logistics": 物流轨迹、异常检测、预计送达
- "coupon": 优惠券查询、最优优惠券推荐、促销活动
- "policy": 售后政策、退货规则、保修条款（知识库检索）

规则：
1. 如果用户只是问候/感谢/简单闲聊，设置 is_chat=true 并提供 chat_reply，tasks 留空
2. 为每个涉及的业务领域创建一个 task，query 应重写为适合该领域 Agent 理解的完整问题
3. 如果同一消息涉及多个领域（如"我的订单到哪了？有什么优惠券？"），创建多个 task
4. 优先使用关键词匹配判断领域归属，不要遗漏用户明确表达的诉求
5. 输出严格 JSON 格式
6. 领域按用户的核心诉求判定：消息里出现订单号不构成追加 order 的理由
7. 物流类问题（轨迹、时效、签收、派送、快递公司）只派 logistics；只有同时要求取消或退款才追加 order
8. 规则解释类问题（售后政策、保修、发票、价保、积分）只派 policy；只有同时要求查询具体订单或物流状态才追加 order/logistics
9. 拿不准时只保留最核心的领域，不要堆砌领域

用户消息: {user_message}"""


async def _plan_node(state: ManagerState, config: RunnableConfig) -> dict:
    """LLM 分析用户消息，输出结构化意图计划。"""
    messages = state.get("messages", [])
    if not messages:
        return {"plan": [], "agent_outputs": []}

    last_msg = messages[-1]
    user_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    # 路由用低温度模型：temperature=0.7 会让同一句话两次拆出不同的计划，
    # 直接影响路由稳定性与准确率（model.py 中 ROUTING_MODEL 专为此设计）
    model = get_routing_model()
    try:
        structured_model = model.with_structured_output(IntentPlan)
        plan: IntentPlan = await structured_model.ainvoke(
            PLAN_PROMPT.format(user_message=user_text)
        )

        if plan.is_chat:
            logging.info("[Manager] 识别为闲聊，直接回复")
            return {
                "plan": [],
                "agent_outputs": [],
                "messages": [AIMessage(content=plan.chat_reply)],
            }

        logging.info("[Manager] 规划 %d 个子任务: %s",
                     len(plan.tasks),
                     [(t.agent, t.query[:40]) for t in plan.tasks])
        return {"plan": [t.model_dump() for t in plan.tasks], "agent_outputs": []}

    except Exception as exc:
        logging.warning("[Manager] Plan 节点失败: %s，回退为空计划", exc)
        return {"plan": [], "agent_outputs": []}


# ---------------------------------------------------------------------------
# Node 2: Execute — 并行执行子任务（接入 _run_sub_agents_parallel）
# ---------------------------------------------------------------------------

async def _run_sub_agents_parallel(tasks: list) -> list:
    """并行执行多个 asyncio Task，统一解析 LangGraph 返回格式。

    输入：asyncio.create_task() 生成的 Task 列表
    输出：解析后的结果列表 [{content, messages} | {error}]
    容错：return_exceptions=True，单个子 Agent 失败不阻塞其他 Agent
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    parsed = []
    for r in results:
        if isinstance(r, Exception):
            parsed.append({"error": str(r)})
        elif isinstance(r, dict) and "messages" in r:
            msgs = r["messages"]
            last = msgs[-1] if msgs else None
            parsed.append({
                "content": getattr(last, "content", "") if last else "",
                "messages": msgs,
            })
        else:
            parsed.append({"content": str(r)})
    return parsed


async def _execute_node(state: ManagerState, config: RunnableConfig) -> dict:
    """遍历 Plan，为领域 Agent 创建 asyncio Task 并调用 _run_sub_agents_parallel。"""
    plan = state.get("plan", [])
    if not plan:
        return {"agent_outputs": []}

    agents = await _ensure_agents()
    agent_tasks: list = []   # 传给 _run_sub_agents_parallel 的 Task 列表
    task_meta: list[dict] = []  # 记录每个 Task 对应的 agent 名
    direct_results: list[dict] = []  # RAG 等直接调用的结果

    for item in plan:
        name = item["agent"]
        query = item["query"]

        # Policy → 直接调用 RAG（非 Agent，无需通过 _run_sub_agents_parallel）
        if name == "policy":
            try:
                content = query_knowledge_base(query)
                direct_results.append({"agent": name, "content": content, "error": None})
            except Exception as exc:
                direct_results.append({"agent": name, "content": "", "error": str(exc)})
            continue

        # 未知 Agent → 记录错误
        runtime = agents.get(name)
        if runtime is None:
            direct_results.append({"agent": name, "content": "", "error": f"未知 Agent: {name}"})
            continue

        # 领域 Agent → 创建 asyncio Task 供 _run_sub_agents_parallel 并行执行
        task = asyncio.create_task(
            runtime.ainvoke({"messages": [HumanMessage(content=query)]}, config=config)
        )
        agent_tasks.append(task)
        task_meta.append({"agent": name, "query": query})

    # 调用已有的并行执行函数
    if agent_tasks:
        logging.info("[Manager] 并行执行 %d 个 Agent 任务", len(agent_tasks))
        parallel_results = await _run_sub_agents_parallel(agent_tasks)
        for meta, result in zip(task_meta, parallel_results):
            direct_results.append({
                "agent": meta["agent"],
                "content": result.get("content", ""),
                "error": result.get("error"),
            })
        # 统计成功/失败
        ok = sum(1 for r in direct_results if not r.get("error"))
        fail = sum(1 for r in direct_results if r.get("error"))
        logging.info("[Manager] 并行执行完成: %d 成功, %d 失败", ok, fail)

    return {"agent_outputs": direct_results}


# ---------------------------------------------------------------------------
# Node 3: Synthesize — LLM 汇总子 Agent 结果，生成统一回复
# ---------------------------------------------------------------------------

SYNTHESIZE_PROMPT = """你是一个客服主管。根据各部门返回的信息，为用户组织一个自然、完整的回复。

各部门返回:
{agent_responses}

要求：
- 用自然的中文对话风格，不要列出"部门A说…部门B说…"
- 按逻辑顺序组织：先回答核心问题，再补充相关信息
- 如果某个部门返回错误，礼貌说明该部分信息暂时无法获取，不要直接展示技术错误信息
- 如果某条信息来自知识库（policy），可适当引用
- 简洁完整，不要啰嗦"""


async def _synthesize_node(state: ManagerState, config: RunnableConfig) -> dict:
    """汇总所有 Agent 输出，由 LLM 生成统一回复。"""
    outputs = state.get("agent_outputs", [])
    if not outputs:
        return {}

    # 构造各部门响应文本
    parts = []
    for o in outputs:
        agent_name = o.get("agent", "unknown")
        error = o.get("error")
        content = o.get("content", "")
        if error:
            parts.append(f"[{agent_name}] 出错: {error}")
        elif content:
            parts.append(f"[{agent_name}]: {content}")

    if not parts:
        return {"messages": [AIMessage(content="抱歉，暂时无法处理您的请求，请稍后重试或联系人工客服。")]}

    model = get_model()
    prompt = SYNTHESIZE_PROMPT.format(agent_responses="\n\n".join(parts))
    response = await model.ainvoke(prompt)
    return {"messages": [AIMessage(content=response.content)]}


# ---------------------------------------------------------------------------
# 构建 Graph
# ---------------------------------------------------------------------------
_manager_graph = None


async def get_manager_agent():
    """创建 Manager Agent (v3): Plan → Execute → Synthesize 三阶段流水线。

    返回 CompiledGraph，兼容 Gateway 的 CompiledSubAgent 包装。
    """
    global _manager_graph
    if _manager_graph is not None:
        return _manager_graph

    builder = StateGraph(ManagerState)
    builder.add_node("plan", _plan_node)
    builder.add_node("execute", _execute_node)
    builder.add_node("synthesize", _synthesize_node)
    builder.set_entry_point("plan")

    # 条件路由：有计划 → 执行；无计划（闲聊）→ 直接结束
    def _route_after_plan(state: ManagerState) -> str:
        return "execute" if state.get("plan") else "synthesize"

    builder.add_conditional_edges("plan", _route_after_plan, {
        "execute": "execute",
        "synthesize": "synthesize",
    })
    builder.add_edge("execute", "synthesize")
    builder.add_edge("synthesize", END)

    _manager_graph = builder.compile()
    logging.info("[Manager] v3 并行编排 Graph 已编译")
    return _manager_graph
