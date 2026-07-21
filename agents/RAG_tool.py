# RAG_tool.py — 知识库查询的轻量封装层
#
# 设计意图：
# - 薄封装：不直接实现检索逻辑，委托给 rag_hybrid 模块
# - 失败降级：检索异常时返回友好的提示信息，不暴露技术细节给用户
# - 统一入口：Manager Agent 中的 get_policy 工具通过此函数查询知识库
# - 为什么不在 Manager Agent 中直接调用 HybridRetriever：分离关注点，
#    此文件负责格式化输出和异常处理，rag_hybrid 负责检索算法
import os  # 文件路径操作

# 定义数据目录（知识库文档存放位置）
DATA_DIR = os.path.join(os.path.dirname(__file__), "../RAG_data")


def query_knowledge_base(query: str) -> str:
    """
    查询知识库并返回格式化后的检索结果。
    
    输入：用户查询字符串（如"退款时效说明"）
    输出：格式化后的知识库内容文本（包含来源标注）
    失败处理：
    - 检索器不可用 → 返回降级提示，引导用户提供更多信息
    - 无匹配文档 → 返回"未找到"提示
    - 检索异常 → 被外层 try/except 捕获，返回通用降级消息
    
    为什么不直接用 LLM 回答而要查知识库：LLM 不知道公司的具体售后政策，
        用 RAG 可以确保回答基于真实政策文档，减少幻觉
    """
    try:
        from agents.rag_backend import get_retriever

        retriever = get_retriever()
        docs = retriever.retrieve(query, top_k=5, final_k=3)  # 召回 5 个 → 重排序后取 3 个
    except Exception:
        # 降级策略：检索失败不中断对话，返回引导性提示
        # 为什么不全返回错误：用户体验优于技术正确性，降级提示让对话可以继续
        return (
            "知识库暂时不可用，当前无法提供准确的售后政策说明。"
            "请稍后重试，或提供更具体的订单号、商品名称、退款/退货问题。"
        )

    if not docs:  # 检索到 0 个相关文档
        return "未在知识库中找到相关信息。"

    # 格式化检索结果：逐条拼接文档内容，标注来源
    result_text = "根据知识库检索到的信息：\n\n"
    for i, doc in enumerate(docs, 1):  # 从 1 开始编号
        source = doc.metadata.get('source', '未知')
        source_name = os.path.basename(source) if source != '未知' else '未知'
        version = doc.metadata.get("knowledge_version", "unversioned")
        result_text += f"--- 相关片段 {i} (来源: {source_name}, 版本: {version}) ---\n"
        result_text += f"{doc.page_content}\n\n"  # 文档内容
    return result_text  # 返回格式化文本供 LLM 参考
