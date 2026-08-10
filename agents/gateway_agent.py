# Gateway Agent
from deepagents import create_deep_agent,CompiledSubAgent
from langchain.agents import create_agent
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents.middleware.todo import TodoListMiddleware
from model import get_model, get_middleware_model
from manager_agent import get_manager_agent
from langchain.agents import AgentState
# 导入总结中间件
from langchain.agents.middleware import SummarizationMiddleware

# 用户上下文状态
class user_state(AgentState):
    """
    用户状态类，继承自AgentState，用于存储用户的对话状态。
    
    Attributes:
        user_id (str): 用户唯一标识符
        conversation_history (list): 对话历史记录，包含用户和助手的消息
        current_question (str): 当前用户正在询问的问题
        order_id (str, optional): 当前用户相关的订单ID，默认为None
    """
    user_id: str

async def get_gateway_agent():
    """
    创建并返回AI客服系统网关智能体实例。
    
    网关智能体负责接收用户输入并转发给总控智能体处理。
    
    Returns:
        langchain.agents.AgentExecutor: 配置好的网关智能体
    """
    # 生产环境必须依赖 Redis 持久化会话；开发环境才允许内存回退。
    import os
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        from langgraph.checkpoint.redis import AsyncRedisSaver
        checkpointer = AsyncRedisSaver(redis_url=REDIS_URL)
        await checkpointer.asetup()
        print("[GATEWAY] AsyncRedisSaver 已连接")
    except Exception as e:
        if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
            raise RuntimeError("生产环境 Redis checkpoint 不可用") from e
        print("[GATEWAY] AsyncRedisSaver 不可用，开发环境回退到 MemorySaver")
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
 
    # 从模型配置中心获取模型实例（整个 Gateway 复用同一个模型）
    model = get_model()

    # 获取总控智能体实例，用于后续组装子智能体中间件
    manager_agent = await get_manager_agent()

    # 配置总结中间件
    # 作用：当对话历史过长时，自动总结之前的对话内容，以减少上下文长度并保留关键信息
    # trigger：上下文累计 32000 token 或消息数达 100 条时触发（以较早者为准）
    #   - tokens 32000：DashScope OpenAI 兼容模型未提供上下文窗口 profile，使用固定阈值以兼容新版 LangChain
    #   - messages 100：兜底——短消息堆积到 100 条时也触发
    # keep：摘要后保留最近 8000 token，确保当前对话轮次不丢失
    # model：使用独立的 middleware 模型（qwen-turbo），避免切换主模型时影响摘要质量与成本
    summarization = SummarizationMiddleware(
        model=get_middleware_model(),
        trigger=[("tokens", 32000), ("messages", 100)],
        keep=("tokens", 8000),
    )
    
    # 子智能体使用 StateBackend，文件仅存在于会话状态中，不访问宿主机文件系统。
    # 作用：允许网关智能体调用子智能体（如 manager-agent）来处理特定任务
    from deepagents.backends import StateBackend
    subagent_middleware = SubAgentMiddleware(
        backend=StateBackend,
        subagents=[
            CompiledSubAgent(
                name="manager-agent",
                description="负责处理所有客户问题的核心智能体，包括订单处理和政策查询",
                runnable=manager_agent
            )
        ]
    )
    
    # 手动组装 TodoListMiddleware (如果需要)
    # 作用：提供待办事项列表功能，帮助智能体跟踪多步任务的执行状态
    todo_middleware = TodoListMiddleware()
    
    # 从 YAML 配置加载系统提示词（支持 A/B 实验分流）
    from prompts.loader import get_prompt_loader
    prompt_loader = get_prompt_loader()
    system_prompt = prompt_loader.load("gateway", company_name="XX电商")

    # 使用 create_agent 而不是 create_deep_agent
    # 这样我们可以完全控制 middleware 列表，避免重复添加默认中间件
    gateway_agent = create_agent(
        model=model,  # 复用已获取的模型实例
        system_prompt=system_prompt,  # 从 YAML 加载的系统提示词
        middleware=[  # 配置中间件列表，按顺序处理请求和响应
            subagent_middleware,  # 子智能体中间件：允许调用 manager-agent 等子智能体处理复杂任务
            summarization,  # 总结中间件：在对话过长时自动总结历史消息以优化上下文
            todo_middleware,  # 待办事项中间件：帮助智能体跟踪和管理多步任务的执行状态
        ],
        checkpointer=checkpointer  # 添加检查点保存器（AsyncRedisSaver 或 MemorySaver），用于持久化对话状态和实现断点续传
    )
    return gateway_agent
