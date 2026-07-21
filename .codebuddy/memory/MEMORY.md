# 项目记忆

## 2026-07-11：为 17 个核心 .py 文件逐行添加详细注释

为《简历&&面试内容&&学习规划.md》第 8 节学习路径中提及的全部 17 个 .py 文件添加了逐行中文注释，注释风格遵循"五个问题"原则（输入、状态、输出、失败处理、为什么选择此方案）：

1. `service/main.py` - 已有完善注释，未修改
2. `agents/gateway_agent.py` - 已有基本注释，未修改
3. `agents/model.py` - 新增逐行注释（模型缓存策略、多模型分离原因）
4. `prompts/loader.py` - 新增逐行注释（A/B 分流、YAML 模板渲染）
5. `agents/manager_agent.py` - 新增逐行注释（并行编排设计、子 Agent 路由）
6. `agents/mcp_wrapper.py` - 新增逐行注释（user_id 注入安全风险说明）
7. `Mcpserver/product_mcp.py` - 新增逐行注释（MCP 设计意图、SQL 参数化）
8. `Mcpserver/logistics_mcp.py` - 新增逐行注释（权限校验、异常检测逻辑）
9. `Mcpserver/coupon_mcp.py` - 新增逐行注释（贪心推荐策略、TTL 差异化）
10. `data/create_db.py` - 新增逐行注释（幂等初始化、多库分离原因）
11. `agents/RAG_tool.py` - 新增逐行注释（薄封装层、降级策略）
12. `agents/rag_hybrid.py` - 新增逐行注释（BM25+FAISS 多路召回、Reranker 重排序）
13. `agents/cache_layer.py` - 新增逐行注释（语义缓存原理、不缓存订单的原因）
14. `agents/stream_callback.py` - 新增逐行注释（SSE 协议、回退机制）
15. `agents/sentiment_model.py` - 新增逐行注释（规则匹配 vs LLM 情感分析对比）
16. `agents/human_handoff.py` - 新增逐行注释（工单状态机、Redis 队列）
17. `agents/langfuse_integration.py` - 新增逐行注释（可观测降级策略）

所有文件通过 linter 检查，无语法错误。
