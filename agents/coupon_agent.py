import os
import sys
from langchain_mcp_adapters.client import MultiServerMCPClient
from model import get_model
from deepagents import create_deep_agent
from mcp_wrapper import wrap_mcp_tools

_mcp_client = None
_mcp_tools = None


async def _get_mcp_tools():
    """异步获取 MCP 工具（单例模式）"""
    global _mcp_client, _mcp_tools

    if _mcp_tools is None:
        if _mcp_client is None:
            mcp_server_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "../Mcpserver/coupon_mcp.py")
            )
            if not os.path.exists(mcp_server_path):
                raise FileNotFoundError(f"MCP 服务器文件不存在: {mcp_server_path}")
            print(f"[DEBUG] 连接 MCP 服务器: {mcp_server_path}")
            _mcp_client = MultiServerMCPClient({
                "coupon_mcp": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [mcp_server_path],
                    "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                }
            })
        _mcp_tools = await _mcp_client.get_tools()
        print(f"[DEBUG] 成功获取 {len(_mcp_tools)} 个优惠券 MCP 工具")
    return _mcp_tools


async def get_coupon_agent():
    """创建优惠券/促销子智能体"""
    model = get_model()
    raw_tools = await _get_mcp_tools()
    wrapped_tools = wrap_mcp_tools(raw_tools)

    from prompts.loader import get_prompt_loader
    system_prompt = get_prompt_loader().load("coupon_agent")

    coupon_agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=wrapped_tools
    )
    return coupon_agent
