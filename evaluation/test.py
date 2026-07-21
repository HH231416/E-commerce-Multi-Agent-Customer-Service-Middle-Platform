import asyncio
import os
import sys
import json
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from agentevals.trajectory.match import create_trajectory_match_evaluator

# 添加路径以便导入 agents
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.path.dirname(__file__), "../agents"))

from agents.gateway_agent import get_gateway_agent

# 1. 定义两个手动构造的测试用例（包含 expected_trajectory）
test_cases = [
    {
        # Case 1: 订单查询
        "question": "查订单A1001",
        "user_id": "1",
        "expected_trajectory": [
            HumanMessage(content="查订单A1001"),
            AIMessage(content="", tool_calls=[
                {"name": "get_order", "args": {"order_no": "A1001", "user_id": "1"}, "id": "any_id"}
            ]),
            # 这里的 ToolMessage 内容不重要，评估器在 unordered 模式下主要看 tool_calls
            ToolMessage(content="...", tool_call_id="any_id", name="get_order"),
            AIMessage(content="...") 
        ]
    },
    {
        # Case 2: 商品查询
        "question": "帮我找找电脑外设",
        "user_id": "4",
        "expected_trajectory": [
            HumanMessage(content="帮我找找电脑外设"),
            AIMessage(content="", tool_calls=[
                {"name": "search_products", "args": {"query": "电脑外设", "user_id": "4"}, "id": "any_id"}
            ]),
            ToolMessage(content="...", tool_call_id="any_id", name="search_products"),
            AIMessage(content="...")
        ]
    }
]

# 2. 创建评估器 (无序模式)
evaluator = create_trajectory_match_evaluator(
    trajectory_match_mode="unordered"
)

async def run_test():
    print("=== 开始本地 Trajectory 评估测试 ===\n")
    agent = await get_gateway_agent()
    
    for i, case in enumerate(test_cases):
        print(f"--- Case {i+1}: {case['question']} ---")
        
        # 1. 运行 Agent
        config = {"configurable": {"thread_id": case["user_id"]}}
        try:
            result = await agent.ainvoke(
                {"messages": [("user", case["question"])]}, 
                config=config
            )
            actual_messages = result["messages"]
            
            # 打印实际调用的工具，方便调试
            print("[Actual Tool Calls]:")
            for msg in actual_messages:
                if isinstance(msg, AIMessage) and msg.tool_calls:
                    print(f"  - {msg.tool_calls}")
                if isinstance(msg, ToolMessage):
                    print(f"  [Tool Output]: {msg.content[:200]}...")

            # 2. 运行评估
            # 注意：outputs 传入实际的消息列表，reference_outputs 传入预期的消息列表
            evaluation_result = evaluator(
                outputs=actual_messages, 
                reference_outputs=case["expected_trajectory"]
            )
            
            print(f"[Evaluation Result]: {evaluation_result}")
            
            if evaluation_result["score"]:
                print("✅ 测试通过")
            else:
                print("❌ 测试失败")
                
        except Exception as e:
            print(f"❌ 运行出错: {e}")
            import traceback
            traceback.print_exc()
        
        print("\n")

if __name__ == "__main__":
    asyncio.run(run_test())
