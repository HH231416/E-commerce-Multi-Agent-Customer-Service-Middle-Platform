import asyncio
import uuid
import os
import sys
import json
import shutil
from langsmith import aevaluate # 使用异步评估函数
from langsmith.schemas import Run, Example
from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage, AIMessage
from dotenv import load_dotenv

# 将项目根目录添加到 python path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
# 将 agents 目录也添加到 python path，以便其中的模块可以相互导入
sys.path.append(os.path.join(os.path.dirname(__file__), "../agents"))

# 加载环境变量
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

# === 数据库沙盒环境配置 ===
ORIGINAL_ORDER_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/orders.db"))
TEST_ORDER_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/orders_test.db"))

ORIGINAL_PRODUCT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/products.db"))
TEST_PRODUCT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/products_test.db"))



# === 导入 Agent (必须在环境变量设置之后) ===
# 注意：由于 Python 的导入机制，如果这里直接导入，可能模块内部已经读取了旧的环境变量。
# 但我们在 main 函数中调用 setup_test_db 后再动态导入或者运行，通常更稳健。
# 这里的 import 放在顶部没问题，因为 MCP Server 是在运行时读取 os.getenv，而不是 import 时。
from agents.gateway_agent import get_gateway_agent

dataset_name = "Ecommerce Multi-Agent Customer Service Platform Test"

def load_examples_from_json(file_path):
    if not os.path.exists(file_path):
        print(f"Warning: JSON file not found at {file_path}")
        return []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    formatted_examples = []
    for item in data:
        formatted_examples.append({
            "inputs": {
                "question": item["question"],
                # 读取 user_id，默认为 "1" 以防万一
                "user_id": item.get("user_id", "1")
            },
            "outputs": {
                "expected_output": item["expected_output"],
                "expected_tool": item.get("expected_tool", "")
            },
            "metadata": {"category": item["category"]}
        })
    return formatted_examples

json_path = os.path.join(os.path.dirname(__file__), "test_dataset.json")
examples = load_examples_from_json(json_path)

# 2. 定义目标函数 (System under test)
async def target(inputs: dict) -> dict:
    """
    运行 Gateway Agent 处理输入并返回结果
    """
    question = inputs["question"]
    print(f"\n[EVAL] 正在测试问题: {question[:50]}...")
    
    try:
        agent = await get_gateway_agent()
        
        # 获取 user_id 作为 thread_id
        # 这样 mcp_wrapper 就能从 config 中提取正确的 user_id
        user_id = inputs.get("user_id", "1")
        config = {"configurable": {"thread_id": user_id}} 
        
        # 增加超时保护，比如 500 秒
        response = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [("user", inputs["question"])]}, 
                config=config
            ),
            timeout=500
        )
        
        last_message = response["messages"][-1].content
        return {
            "response": last_message,
            "messages": response["messages"]  # 关键：返回完整历史以便检查工具调用
        }
    except asyncio.TimeoutError:
        return {"response": "Error: Local execution timed out (300s)", "messages": []}
    except Exception as e:
        return {"response": f"Error: {str(e)}", "messages": []}

# 3. 定义评估器
eval_llm = ChatOpenAI(
    model="deepseek-ai/deepdeek-v4-pro", 
    api_key=os.getenv("SILICONFLOW_API_KEY"),
    base_url="https://api.deepseek.com"
)

def correctness_evaluator(run: Run, example: Example) -> dict:
    student_answer = run.outputs.get("response")
    expected_answer = example.outputs.get("expected_output")
    question = example.inputs.get("question")
    category = example.metadata.get("category", "general")

    prompt = f"""
    你是一个评估专家。请判断 AI 的回答是否满足了用户的需求以及是否符合预期的标准。
    
    场景分类: {category}
    用户问题: {question}
    AI 回答: {student_answer}
    期望/参考标准: {expected_answer}

    如果在语义上一致，或者 AI 回答包含了期望的关键信息，请返回 Score: 1。
    如果完全不相关或错误，返回 Score: 0。
    
    请只返回一个数字：0 或 1。
    """
    
    try:
        response = eval_llm.invoke(prompt)
        score = int(response.content.strip())
        return {"key": "correctness", "score": score}
    except:
        return {"key": "correctness", "score": 0}

def tool_usage_evaluator(run: Run, example: Example) -> dict:
    """
    白盒检查：遍历消息历史，确认是否调用了预期工具
    """
    expected_tool = example.outputs.get("expected_tool")
    
    # 如果数据集里没规定要用工具，默认满分（或跳过）
    if not expected_tool:
        return {"key": "tool_usage", "score": None}

    # 从 run.outputs 中获取我们在 target 里返回的 messages
    # 注意：LangSmith 传给 evaluator 的 run.outputs 就是 target 的返回值
    messages = run.outputs.get("messages", [])
    
    tool_found = False
    
    for msg in messages:
        # 方法 A: 检查 AIMessage 的 tool_calls (意图)
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call['name'] == expected_tool:
                    tool_found = True
                    break
        
        if tool_found:
            break
            
    return {
        "key": "tool_usage", 
        "score": 1 if tool_found else 0
    }

async def main():
    try:
        
        
        print("开始运行评估...")
        from langsmith import Client
        client = Client()
        
        # 强制更新数据集：如果已存在，可以选择覆盖或跳过
        # 为了确保 user_id 字段生效，建议删除旧的或使用新名称
        # 这里我们简单起见，如果存在就更新 examples
        if not client.has_dataset(dataset_name=dataset_name):
            dataset = client.create_dataset(dataset_name=dataset_name)
            client.create_examples(
                inputs=[e["inputs"] for e in examples],
                outputs=[e["outputs"] for e in examples],
                metadata=[e["metadata"] for e in examples],
                dataset_id=dataset.id,
            )
            print(f"已创建新数据集: {dataset_name}")
        else:
            # 如果数据集已存在，但我们修改了本地 JSON 结构（加了 user_id），
            # 最好的办法是创建一个新的 Dataset Version，或者在 LangSmith 上手动删除。
            # 为了代码简单，这里打印提示。
            print(f"警告: 使用现有数据集 {dataset_name}。如果这是您第一次添加 user_id，请在 LangSmith 上删除旧数据集以便重新创建。")
            # 尝试更新数据（LangSmith API 允许向现有数据集添加新 examples，但不自动删除旧的）

        results = await aevaluate(
            target,
            data=dataset_name,
            evaluators=[correctness_evaluator, tool_usage_evaluator], # 添加了工具评估
            experiment_prefix="gateway-agent-test",
            metadata={"version": "1.0.0"},
            max_concurrency=1  # 限制并发以避免 Rate Limit
        )
        
        print("\n评估完成！请访问 LangSmith 控制台查看详细报告。")
        print(results)
        
    finally:
        pass

if __name__ == "__main__":
    asyncio.run(main())
