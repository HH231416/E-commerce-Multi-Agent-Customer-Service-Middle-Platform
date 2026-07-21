import os
import sys
from langchain_mcp_adapters.client import MultiServerMCPClient
from model import get_model
from deepagents import create_deep_agent
from mcp_wrapper import wrap_mcp_tools

# MCP 客户端（全局变量）
_mcp_client = None
_mcp_tools = None

async def _get_mcp_tools():
    """异步获取 MCP 工具"""
    global _mcp_client, _mcp_tools
    
    if _mcp_tools is None:
        # 初始化客户端
        if _mcp_client is None:
            mcp_server_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../Mcpserver/product_mcp.py")
            )
            
            if not os.path.exists(mcp_server_path):
                raise FileNotFoundError(f"MCP 服务器文件不存在: {mcp_server_path}")
            
            print(f"[DEBUG] 连接 MCP 服务器: {mcp_server_path}")
            
            _mcp_client = MultiServerMCPClient({
                "product_mcp": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [mcp_server_path],
                    "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                }
            })
        
        # 异步获取工具
        _mcp_tools = await _mcp_client.get_tools()
        print(f"[DEBUG] 成功获取 {len(_mcp_tools)} 个 MCP 工具")
    
    return _mcp_tools

async def get_product_agent():
    """
    创建并返回商品查询智能体实例。
    
    商品智能体负责处理与商品相关的所有问题，包括查询商品信息、价格、库存等。
    
    Returns:
        DeepAgent: 配置好的商品查询智能体
    """
    # 1. 从模型配置中心获取模型实例
    model = get_model()
    # 2. 获取原始 MCP 工具
    raw_tools = await _get_mcp_tools()

    # 3. 包装工具 (自动注入 user_id)
    wrapped_tools = wrap_mcp_tools(raw_tools)

    # 从 YAML 配置加载系统提示词
    from prompts.loader import get_prompt_loader
    system_prompt = get_prompt_loader().load("product_agent")

    product_agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=wrapped_tools       
    )
    return product_agent
