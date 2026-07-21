# MCP 工具包装器：自动注入 user_id 并对 LLM 隐藏敏感参数
#
# 设计意图：
# - MCP Server 的 tool 需要 user_id 做权限校验，但 LLM 不应该知道/传递这个参数
#   （如果 LLM 可以伪造 user_id，就能越权查看其他用户的订单/物流数据）
# - 通过包装器，从 LangGraph 的 RunnableConfig 中提取 thread_id 作为 user_id
# - 对 LLM 展示的 Schema 中移除 user_id 字段，运行时自动注入
# - 为什么不直接在 MCP Server 侧处理：MCP Server 是独立的进程/服务，不知道 LangGraph 的上下文；
#     在 Agent 侧包装更合理，因为 thread_id 就是在这里绑定的
from langchain_core.tools import StructuredTool  # LangChain 结构化工具基类
from langchain_core.runnables import RunnableConfig  # LangGraph 运行时配置（包含 thread_id）
from pydantic import create_model, BaseModel  # 动态创建 Pydantic Schema 模型

def wrap_mcp_tool_with_user_id(original_tool: StructuredTool):
    """
    包装 MCP 工具：
    1. 修改 Schema：对 LLM 隐藏 user_id 参数（防止 LLM 伪造身份越权访问）
    2. 运行时注入：从 config 中自动提取 thread_id 作为 user_id
    3. 调用原始工具：将注入后的完整参数传递给 MCP Server
    
    输入：原始 MCP 工具（StructuredTool 实例，args_schema 包含 user_id）
    输出：包装后的新工具（args_schema 不包含 user_id）
    失败处理：无法提取 user_id 时使用默认值 "1"（仅用于开发/测试环境）
    
    安全风险说明：
    - thread_id 来自 main.py 的 config={"configurable": {"thread_id": user_id}}
    - 如果 user_id 由客户端传入且未经认证，则存在身份伪造风险
    - 生产环境应在此处加入 JWT/session 验证，而不是直接信任 thread_id
    """
    
    # 定义包装后的执行函数（async 协程）
    async def wrapped_func(config: RunnableConfig, **kwargs):
        # 用户身份由 API 认证层写入配置，绝不从模型输入或 URL 参数获取。
        user_id = config.get("configurable", {}).get("user_id")
        
        # 生产环境缺少认证上下文必须失败，禁止退回到固定用户。
        if not user_id:
            raise PermissionError("MCP 工具调用缺少已认证用户上下文")
        
        # 构造包含 user_id 的完整参数（LLM 传入的参数 + 系统注入的 user_id）
        input_args = kwargs.copy()  # 复制 LLM 传入的参数
        input_args["user_id"] = user_id  # 注入 user_id
        
        # 调用原始 MCP 工具（传递完整参数）
        # 注意：MCP 工具通常是 StructuredTool，直接调用 ainvoke
        return await original_tool.ainvoke(input_args, config=config)

    # 动态创建新的参数 Schema（排除 user_id 字段）
    # 为什么要动态创建而非手写：每个 MCP tool 的参数不同，需要保留原有字段的类型信息
    old_schema = original_tool.args_schema  # 获取原始工具的参数 Schema
    new_fields = {}  # 新 Schema 的字段字典

    if isinstance(old_schema, dict):  # 如果是字典类型 (JSON Schema 格式)
        properties = old_schema.get("properties", {})  # 获取所有属性定义
        for name, prop in properties.items():  # 遍历每个属性
            if name != "user_id":  # 跳过 user_id 字段（对 LLM 隐藏）
                # 简单处理：这里我们假设都是 string，实际可能需要更复杂的类型映射
                # 但对于 MCP 工具来说，大部分基本类型可以简化处理
                # 如果能获取到 python 类型最好，但在 dict schema 下很难
                # 这里使用 object 或 string 作为通用类型
                new_fields[name] = (str, ...)  # 默认为必填字符串，简化处理
    elif issubclass(old_schema, BaseModel):  # 如果是 Pydantic 模型（更常见）
        for name, field in old_schema.model_fields.items():  # 遍历 Pydantic 字段
            if name != "user_id":  # 跳过 user_id
                new_fields[name] = (field.annotation, field)  # 保留原始类型注解和 Field 定义
    else:
        # 其他情况，直接复制原 schema (如果不包含 user_id)
        # 或者抛出异常
        raise TypeError(f"无法安全解析工具参数模型: {original_tool.name}")

    # 使用 create_model 创建新的 Pydantic 模型（动态生成类）
    NewSchema = create_model(f"{original_tool.name}Input", **new_fields)
    
    # 返回包装后的新工具（使用新的 Schema，隐藏 user_id）
    return StructuredTool.from_function(
        coroutine=wrapped_func,  # 异步执行函数
        name=original_tool.name,  # 保持原工具名称
        description=original_tool.description,  # 保持原工具描述
        args_schema=NewSchema  # 使用不含 user_id 的新 Schema
    )

def wrap_mcp_tools(tools: list[StructuredTool]) -> list[StructuredTool]:
    """批量包装工具列表，对每个工具调用 wrap_mcp_tool_with_user_id"""
    return [wrap_mcp_tool_with_user_id(tool) for tool in tools]  # 列表推导式批量包装
