import os
import sys
# 导入多服务器MCP客户端，用于连接和管理MCP服务
from langchain_mcp_adapters.client import MultiServerMCPClient
# 导入获取大语言模型实例的函数
from model import get_model
# 导入创建深度智能体的函数
from deepagents import create_deep_agent
# 导入MCP工具包装器，用于自动注入用户上下文等参数
from mcp_wrapper import wrap_mcp_tools

# MCP 客户端实例（全局变量，用于单例模式，避免重复初始化）
_mcp_client = None
# MCP 工具列表（全局变量，缓存获取到的工具，避免重复请求）
_mcp_tools = None

async def _get_mcp_tools():
    """异步获取 MCP 工具"""
    # 声明使用全局变量 _mcp_client 和 _mcp_tools
    global _mcp_client, _mcp_tools
    
    # 检查工具是否已经初始化，实现懒加载
    if _mcp_tools is None:
        # 如果客户端未初始化，则进行初始化
        if _mcp_client is None:
            # 构建MCP服务器脚本的绝对路径
            mcp_server_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../Mcpserver/order_mcp.py")
            )
            
            # 检查MCP服务器文件是否存在，不存在则抛出异常
            if not os.path.exists(mcp_server_path):
                raise FileNotFoundError(f"MCP 服务器文件不存在: {mcp_server_path}")
            
            # 打印调试信息，显示正在连接的MCP服务器路径
            print(f"[DEBUG] 连接 MCP 服务器: {mcp_server_path}")
            
            # 初始化 MultiServerMCPClient
            # 配置 order_mcp 服务，使用 stdio 传输协议
            # command: 使用当前Python解释器执行
            # args: 传入MCP服务器脚本路径
            # env: 设置环境变量以确保UTF-8编码正确处理
            _mcp_client = MultiServerMCPClient({
                "order_mcp": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [mcp_server_path],
                    "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                }
            })
        
          # 异步调用客户端方法获取所有可用的MCP工具
        _mcp_tools = await _mcp_client.get_tools()
        # 打印调试信息，显示成功获取的工具数量
        print(f"[DEBUG] 成功获取 {len(_mcp_tools)} 个 MCP 工具")
    
    # 返回缓存的工具列表
    return _mcp_tools

async def get_order_agent():
    """
    创建并返回订单子智能体实例。
    
    订单子智能体负责处理订单相关问题，包括查询状态、取消订单、退款等。
    
    Returns:
        DeepAgent: 配置好的订单子智能体
    """
    # 1. 从模型配置中心获取模型实例
    model = get_model()
    # 2. 异步获取原始 MCP 工具列表
    raw_tools = await _get_mcp_tools()

    # 3. 使用包装器处理工具，自动注入 user_id 等上下文信息
    wrapped_tools = wrap_mcp_tools(raw_tools)

    # 从 YAML 配置加载系统提示词
    from prompts.loader import get_prompt_loader
    system_prompt = get_prompt_loader().load("order_agent")

    # 创建深度智能体实例
    order_agent = create_deep_agent(
        model=model, # 指定使用的大语言模型
        system_prompt=system_prompt,
        tools=wrapped_tools # 注入包装后的工具列表
    )
    
    # 返回配置好的订单智能体实例
    return order_agent