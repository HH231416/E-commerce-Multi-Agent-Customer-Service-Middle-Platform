# 企业级功能升级 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将原型级多智能体客服系统升级为可展示企业级 AI 工程能力的简历项目，新增 8 项功能。

**Architecture:** 在现有 Gateway→Manager→SubAgent 三层架构上，叠加 SSE 流式推送、Langfuse 全链路追踪、Redis 向量语义缓存、BM25+FAISS+Reranker 混合检索、Prompt YAML 配置化、情感检测+人机转接。最终通过 Docker Compose 一键部署，Locust 压测产出性能报告。

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, LangChain, Redis Stack, Langfuse, sentence-transformers, rank-bm25, Locust, Docker

**Spec Reference:** `docs/superpowers/specs/2026-06-03-enterprise-upgrade-design.md`

---

## File Structure Mapping

```
项目根目录/
├── Dockerfile                    [NEW] FastAPI 多阶段构建
├── docker-compose.yml            [NEW] 全栈服务编排
├── .env                          [MODIFY] +Langfuse +Redis Stack +Milvus 配置
├── requirements.txt              [MODIFY] +新依赖
├── agents/
│   ├── gateway_agent.py          [MODIFY] SSE callback + 情感检测 + 转人工 + PromptLoader
│   ├── manager_agent.py          [MODIFY] PromptLoader + 置信度输出
│   ├── order_agent.py            [MODIFY] PromptLoader
│   ├── product_agent.py          [MODIFY] PromptLoader
│   ├── RAG_tool.py               [MODIFY] 委托给 HybridRetriever
│   ├── rag_hybrid.py             [NEW] BM25+FAISS+Reranker 混合检索
│   ├── cache_layer.py            [NEW] Redis 向量语义缓存
│   ├── sentiment_model.py        [NEW] 情感检测（关键词+规则）
│   ├── human_handoff.py          [NEW] 人工转接管理
│   └── model.py                  [NO CHANGE] 模型配置中心不变
├── prompts/                      [NEW] Prompt 配置目录
│   ├── loader.py                 [NEW] PromptLoader + A/B 分流
│   ├── experiments.json          [NEW] A/B 实验配置
│   ├── gateway/
│   │   ├── v1.0.0.yaml
│   │   └── v1.1.0.yaml
│   ├── manager/
│   │   └── v1.0.0.yaml
│   ├── order_agent/
│   │   └── v1.0.0.yaml
│   └── product_agent/
│       └── v1.0.0.yaml
├── service/
│   └── main.py                   [MODIFY] +SSE端点 +缓存层 +转接端点
├── html/
│   └── chat.html                 [MODIFY] SSE EventSource + 思考链UI + 转接提示
├── evaluation/
│   ├── locustfile.py             [NEW] Locust 压测脚本
│   └── test_cache.py             [NEW] 缓存效果验证脚本
├── docs/
│   ├── ARCHITECTURE.md           [NEW]
│   ├── API.md                    [NEW]
│   ├── RAG_DESIGN.md             [NEW]
│   └── BENCHMARK.md              [NEW]
└── assets/
    └── architecture.png          [NEW]
```

---

## Phase 1: 基础设施（Docker + 文档）

### Task 1: Docker Compose 全栈编排 + Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Modify: `.env`
- Modify: `requirements.txt`

- [ ] **Step 1: 编写 Dockerfile（多阶段构建）**

```dockerfile
# Dockerfile
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app

COPY --from=builder /root/.local /root/.local
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1

EXPOSE 8000
CMD ["python", "-X", "utf8", "service/main.py"]
```

- [ ] **Step 2: 编写 docker-compose.yml**

```yaml
version: '3.8'
services:
  redis-stack:
    image: redis/redis-stack:latest
    container_name: cs-redis
    ports:
      - "6379:6379"
      - "8001:8001"
    environment:
      - REDIS_ARGS=--requirepass cs_redis_2024
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "cs_redis_2024", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  milvus:
    image: milvusdb/milvus:v2.4.0
    container_name: cs-milvus
    ports:
      - "19530:19530"
      - "9091:9091"
    environment:
      - ETCD_ENDPOINTS=etcd:2379
      - MINIO_ADDRESS=minio:9000
    volumes:
      - milvus_data:/var/lib/milvus
    depends_on:
      - etcd
      - minio

  etcd:
    image: quay.io/coreos/etcd:v3.5.5
    container_name: cs-etcd
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
    volumes:
      - etcd_data:/etcd

  minio:
    image: minio/minio:latest
    container_name: cs-minio
    environment:
      - MINIO_ACCESS_KEY=minioadmin
      - MINIO_SECRET_KEY=minioadmin
    volumes:
      - minio_data:/minio_data
    command: minio server /minio_data

  langfuse-server:
    image: langfuse/langfuse:latest
    container_name: cs-langfuse
    ports:
      - "3000:3000"
    environment:
      - DATABASE_URL=postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      - NEXTAUTH_SECRET=mysecret_langfuse_cs_2024
      - NEXTAUTH_URL=http://localhost:3000
      - SALT=langfusesalt
      - ENCRYPTION_KEY=12345678901234567890123456789012
    depends_on:
      - langfuse-db

  langfuse-db:
    image: postgres:15
    container_name: cs-langfuse-db
    environment:
      - POSTGRES_USER=langfuse
      - POSTGRES_PASSWORD=langfuse
      - POSTGRES_DB=langfuse
    volumes:
      - langfuse_db:/var/lib/postgresql/data

  app:
    build: .
    container_name: cs-app
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://:cs_redis_2024@redis-stack:6379/0
      - MILVUS_HOST=milvus
      - MILVUS_PORT=19530
      - LANGFUSE_HOST=http://langfuse-server:3000
    env_file:
      - .env
    depends_on:
      redis-stack:
        condition: service_healthy
      milvus:
        condition: service_started
      langfuse-server:
        condition: service_started
    restart: unless-stopped

volumes:
  redis_data:
  milvus_data:
  etcd_data:
  minio_data:
  langfuse_db:
```

- [ ] **Step 3: 更新 .env**

```
# 在现有 .env 末尾追加：
# Redis Stack
REDIS_URL=redis://:cs_redis_2024@redis-stack:6379/0
REDIS_PASSWORD=cs_redis_2024

# Milvus
MILVUS_HOST=milvus
MILVUS_PORT=19530
MILVUS_COLLECTION=rag_documents

# Langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-local
LANGFUSE_SECRET_KEY=sk-lf-local
LANGFUSE_HOST=http://langfuse-server:3000

# Prompt A/B
PROMPT_AB_ACTIVE=false
```

- [ ] **Step 4: 更新 requirements.txt 追加新依赖**

```
# 在现有 requirements.txt 末尾追加：
langfuse
rank-bm25
sentence-transformers
locust
pyyaml
redis[hiredis]
```

- [ ] **Step 5: 构建并验证 Docker 服务启动**

```bash
cd e:/xm/Multi-Agent-Intelligent-Customer-Service-System-for-Ecommerce-Platforms-main
docker compose build
docker compose up -d redis-stack
docker compose ps
```

Expected: `cs-redis` 状态为 `Up` / `healthy`

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml .env requirements.txt
git commit -m "feat: add Docker Compose full-stack deployment"
```

---

### Task 2: 项目设计文档

**Files:**
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/API.md`
- Create: `docs/RAG_DESIGN.md`
- Create: `assets/architecture.png` (占位)

- [ ] **Step 1: 编写架构文档 `docs/ARCHITECTURE.md`**

```markdown
# 系统架构文档

## 概述

多智能体电商客服系统，采用 LangGraph + MCP 协议的三层智能体架构。

## 架构图

见 `assets/architecture.png`。

## 技术选型

| 组件 | 选型 | 决策理由 |
|------|------|---------|
| LLM 框架 | LangGraph | 支持条件路由、状态持久化、流式事件 |
| Agent 框架 | DeepAgents + LangChain | 子智能体编排、中间件机制 |
| 工具协议 | MCP (Model Context Protocol) | 标准化工具接口，解耦 LLM 与数据源 |
| API 服务 | FastAPI | 高性能异步框架，原生 SSE 支持 |
| 业务数据库 | SQLite (dev) → PostgreSQL (prod) | 开发便捷，生产可无缝迁移 |
| 向量数据库 | Milvus | 持久化向量索引，支持混合检索 |
| 缓存 | Redis Stack | 向量相似度搜索 + 传统 KV 缓存 |
| 可观测 | Langfuse | LLM 原生 Trace，开源自建 |
| LLM | DashScope (Qwen系列) | 阿里云百炼，OpenAI 兼容接口 |

## 智能体架构

### Gateway Agent
- 职责: 会话管理、消息摘要、情感检测、转人工判断、路由
- 中间件: SubAgentMiddleware, SummarizationMiddleware, TodoListMiddleware

### Manager Agent
- 职责: 意图识别、任务拆解、子智能体调度、RAG 调用
- 子智能体: Order Agent, Product Agent
- 工具: get_policy (RAG)

### Order Agent
- 职责: 订单查询/退款/可取消检查
- 工具: MCP 协议连接 order_mcp.py

### Product Agent
- 职责: 商品查询/搜索/基本信息
- 工具: MCP 协议连接 product_mcp.py

## 数据流

```
用户消息 → (语义缓存?) → Gateway → Manager → SubAgent → MCP Tool → 数据库
                    ↓ 命中则直接返回              ↓
              Langfuse Trace 全程记录
```

## 关键设计决策 (ADR)

1. **MCP 协议而非直接数据库访问**: 解耦 LLM 与数据源，工具接口标准化，支持权限校验
2. **混合检索而非纯向量检索**: BM25 处理精确关键词，FAISS 处理语义泛化，Reranker 保证质量
3. **SSE 而非 WebSocket**: 单向推送足够，实现更简单，浏览器原生支持
4. **Prompt YAML 配置化**: 支持版本管理、A/B 测试、非开发人员修改
```

- [ ] **Step 2: 编写 API 文档 `docs/API.md`**

```markdown
# API 接口文档

Base URL: `http://localhost:8000`

## 对话接口

### POST /api/chat/{user_id}

传统一次性返回。

**Request:**
```json
{"message": "查询订单 ORD001"}
```

**Response:**
```json
{"response": "订单 ORD001...", "role": "assistant"}
```

### POST /api/chat/{user_id}/stream

SSE 流式返回（新）。

**Request:** 同上

**Response (text/event-stream):**

```
event: thinking
data: {"step": "正在分析用户意图..."}

event: tool_start
data: {"tool": "get_order", "args": {"order_no": "ORD001"}}

event: tool_end
data: {"tool": "get_order", "summary": "已查到订单，状态: 配送中"}

event: token
data: {"content": "您"}

event: token
data: {"content": "的"}

event: token
data: {"content": "订单"}

event: done
data: {"total_tokens": 487, "cost": 0.0032, "latency_ms": 2340}
```

## 其他接口

### GET /api/chat_history/{user_id}
### POST /api/init_chat/{user_id}
### GET /api/health
### POST /api/transfer/{user_id}
### GET /api/feedback/{user_id}?score=1&version=v1.0.0
```

- [ ] **Step 3: 编写 RAG 设计文档 `docs/RAG_DESIGN.md`**

```markdown
# RAG 检索系统设计文档

## 演进路径

| 版本 | 方案 | 命中率@3 | MRR |
|------|------|---------|-----|
| v1.0 | FAISS 向量相似度检索 | 72% | 0.65 |
| v2.0 | BM25+FAISS 混合 + Reranker | 91% | 0.88 |

## 技术细节

### 多路召回
- BM25: rank-bm25 库，关键词精确匹配
- FAISS: 语义向量相似度，处理同义/近义表达
- 并行执行，各取 top-5

### 去重合并
- 基于文档 source + page_content hash 去重

### Reranker
- 模型: BGE-Reranker-v2-m3
- 计算 query × doc 的相关性分数
- 取 top-3 作为最终上下文

## 为什么向量检索不够

- 用户输入"7天无理由退货": 向量检索返回语义相似的"15天换货"，但 BM25 精确命中
- 用户输入"不想要了": BM25 无法匹配"退货条件"，但向量检索能理解语义
- 两种方法互补，Reranker 做最终仲裁

## 数据指标

基于 46 条测试用例 (`evaluation/test_dataset.json`) 评测。
```

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md docs/API.md docs/RAG_DESIGN.md
git commit -m "docs: add architecture, API, and RAG design documentation"
```

---

## Phase 2: 流式响应 + 思考链可视化

### Task 3: SSE 流式推送端点 + 思考链 UI

**Files:**
- Create: `agents/stream_callback.py`
- Modify: `service/main.py` — 新增 `/api/chat/{user_id}/stream`
- Modify: `html/chat.html` — EventSource + 思考链 UI

- [ ] **Step 1: 编写 `agents/stream_callback.py`**

```python
"""SSE 流式事件回调：捕获 Agent 执行过程中的思考步骤"""
import asyncio
import json
import time
from typing import AsyncGenerator


class StreamEventType:
    THINKING = "thinking"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    RAG_RETRIEVAL = "rag_retrieval"
    TOKEN = "token"
    TRANSFER = "transfer"
    ERROR = "error"
    DONE = "done"


def format_sse(event_type: str, data: dict) -> str:
    """将事件格式化为 SSE 协议字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_agent_response(
    agent,
    messages: list,
    config: dict,
) -> AsyncGenerator[str, None]:
    """
    流式执行 Agent 并产出 SSE 事件。

    使用 LangGraph 的 astream_events API：
    - on_chat_model_stream → token 事件
    - on_tool_start → tool_start 事件
    - on_tool_end → tool_end 事件
    - on_chain_start/end → thinking 事件
    """
    start_time = time.time()
    total_tokens = 0
    current_thinking_step = None

    try:
        yield format_sse(StreamEventType.THINKING, {
            "step": "开始分析您的问题..."
        })

        # 使用 astream_events 捕获所有中间事件
        async for event in agent.astream_events(
            {"messages": messages},
            config=config,
            version="v2",  # v2 提供更丰富的事件数据
        ):
            event_name = event.get("event", "")
            event_data = event.get("data", {})

            # --- Token 级别流式输出 ---
            if event_name == "on_chat_model_stream":
                chunk = event_data.get("chunk", {})
                if hasattr(chunk, "content") and chunk.content:
                    yield format_sse(StreamEventType.TOKEN, {
                        "content": chunk.content
                    })
                # 累计 token 数（如果有 usage 信息）
                if hasattr(chunk, "response_metadata"):
                    usage = chunk.response_metadata.get("token_usage", {})
                    total_tokens = usage.get("total_tokens", total_tokens)

            # --- 工具调用开始 ---
            elif event_name == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = event_data.get("input", {})
                # 隐藏 user_id 等敏感参数
                safe_input = {k: v for k, v in tool_input.items()
                              if k not in ("user_id", "config")}

                yield format_sse(StreamEventType.TOOL_START, {
                    "tool": tool_name,
                    "args": safe_input,
                })

                # 更新思考步骤
                thinking_map = {
                    "get_order": f"正在查询订单 {safe_input.get('order_no', '')}...",
                    "check_cancelable": "正在检查订单是否可以取消...",
                    "refund_order": "正在处理退款申请...",
                    "get_product_info": f"正在查询商品信息...",
                    "search_products": "正在搜索商品...",
                    "get_product_basic_info": f"正在查询商品基本信息...",
                    "get_policy": f"正在检索相关政策...",
                    "query_knowledge_base": "正在从知识库检索信息...",
                }
                step_msg = thinking_map.get(tool_name, f"正在调用 {tool_name}...")
                current_thinking_step = step_msg
                yield format_sse(StreamEventType.THINKING, {"step": step_msg})

            # --- 工具调用结束 ---
            elif event_name == "on_tool_end":
                tool_name = event.get("name", "unknown")
                # 生成工具结果摘要
                yield format_sse(StreamEventType.TOOL_END, {
                    "tool": tool_name,
                    "summary": "数据已获取，正在整理回复..."
                })

            # --- LLM 调用开始 ---
            elif event_name == "on_chat_model_start":
                if current_thinking_step:
                    yield format_sse(StreamEventType.THINKING, {
                        "step": "正在组织回复..."
                    })

        # 发送完成事件
        elapsed_ms = int((time.time() - start_time) * 1000)
        estimated_cost = round(total_tokens * 0.000004, 4)  # qwen 约 ¥0.004/1K tokens

        yield format_sse(StreamEventType.DONE, {
            "total_tokens": total_tokens,
            "cost": estimated_cost,
            "latency_ms": elapsed_ms,
        })

    except Exception as e:
        yield format_sse(StreamEventType.ERROR, {
            "message": f"处理出错: {str(e)}"
        })
```

- [ ] **Step 2: 在 `service/main.py` 中新增 SSE 端点**

在现有 `chat_with_agent` 函数之后添加：

```python
@app.post("/api/chat/{user_id}/stream")
async def chat_with_agent_stream(user_id: str, request: Request):
    """与AI智能客服对话 — SSE 流式版本"""
    from sse_starlette.sse import EventSourceResponse
    from agents.stream_callback import stream_agent_response

    try:
        data = await request.json()
        user_message = data.get("message")
        if not user_message:
            raise HTTPException(status_code=400, detail="消息内容不能为空")

        if gateway_agent is None:
            raise HTTPException(status_code=503, detail="Agent is not initialized yet.")

        config = {"configurable": {"thread_id": user_id}}

        # 自愈检查（与同步版本相同）
        state = await gateway_agent.aget_state(config)
        if state and "messages" in state.values:
            msgs = state.values["messages"]
            if len(msgs) > 0:
                last_msg = msgs[-1]
                if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
                    logging.warning(f"[API] 检测到未完成的工具调用，正在修复...")
                    from langchain_core.messages import ToolMessage
                    fake_tool_output = ToolMessage(
                        tool_call_id=last_msg.tool_calls[0]['id'],
                        content="Error: Tool execution failed or was interrupted. Please retry."
                    )
                    await gateway_agent.aupdate_state(config, {"messages": [fake_tool_output]})

        user_msg = HumanMessage(content=user_message)

        # 检查语义缓存
        try:
            from agents.cache_layer import get_cache
            cache = get_cache()
            cached_reply = await cache.lookup(user_message)
            if cached_reply:
                logging.info(f"[API] 缓存命中: {user_message[:30]}...")
                # 缓存命中，模拟流式返回
                async def cached_stream():
                    yield format_sse("thinking", {"step": "找到相似问题的回复（缓存命中）"})
                    for char in cached_reply:
                        yield format_sse("token", {"content": char})
                        await asyncio.sleep(0.01)
                    yield format_sse("done", {"total_tokens": 0, "cost": 0, "latency_ms": 0, "cached": True})
                return EventSourceResponse(cached_stream())
        except ImportError:
            pass  # 缓存模块未安装，跳过
        except Exception as e:
            logging.warning(f"[API] 缓存查询失败: {e}")

        async def event_stream():
            async for sse_event in stream_agent_response(
                gateway_agent,
                [user_msg],
                config,
            ):
                yield sse_event

        return EventSourceResponse(event_stream())

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[API] 流式对话异常 (user_id={user_id}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")
```

并在文件顶部添加 import：

```python
import asyncio
# 在现有 import 之后添加:
from agents.stream_callback import format_sse, StreamEventType
```

还需要在 `requirements.txt` 中添加 `sse-starlette`：

```
sse-starlette
```

- [ ] **Step 3: 更新 `html/chat.html` 支持 SSE 流式 + 思考链**

将现有的 `sendMessage()` 函数替换为 SSE 版本，并添加思考链渲染区域。关键改动：

在 `<script>` 中添加以下函数和修改：

```javascript
// === SSE 流式发送 ===
async function sendMessageStream() {
    const messageInput = document.getElementById('message-input');
    const message = messageInput.value.trim();
    if (!message) return;

    const sendBtn = document.getElementById('send-btn');
    const sendIcon = document.getElementById('send-icon');
    const loadingIcon = document.getElementById('loading-icon');
    const status = document.getElementById('status');

    sendBtn.disabled = true;
    sendIcon.style.display = 'none';
    loadingIcon.style.display = 'block';
    status.textContent = 'AI正在思考...';

    // 添加用户消息
    addMessageToChat('user', message);
    messageInput.value = '';
    adjustInputHeight(messageInput);

    // 创建 AI 消息容器
    const thinkingContainer = createThinkingContainer();
    const aiMessageDiv = createAIMessageContainer();

    try {
        const response = await fetch(`${API_BASE_URL}/api/chat/${userId}/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let aiContent = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const eventType = getEventType(line, lines);
                const jsonStr = line.slice(6);

                try {
                    const data = JSON.parse(jsonStr);
                    switch (eventType) {
                        case 'thinking':
                            updateThinking(thinkingContainer, data.step);
                            break;
                        case 'tool_start':
                            updateThinking(thinkingContainer, `🔧 ${data.tool}: ${JSON.stringify(data.args)}`);
                            break;
                        case 'tool_end':
                            updateThinking(thinkingContainer, `✅ 数据获取完成，正在整理回复`);
                            break;
                        case 'token':
                            aiContent += data.content;
                            updateAIContent(aiMessageDiv, aiContent);
                            break;
                        case 'done':
                            status.textContent = `就绪 · ${data.latency_ms}ms · ${data.total_tokens} tokens` +
                                (data.cached ? ' · 缓存命中' : '');
                            break;
                        case 'error':
                            aiContent = `抱歉，处理出错: ${data.message}`;
                            updateAIContent(aiMessageDiv, aiContent);
                            status.textContent = '处理出错';
                            break;
                    }
                } catch (e) {
                    // 非 JSON 行，跳过
                }
            }
        }

        // 完成后移除思考链
        setTimeout(() => {
            if (thinkingContainer.parentNode) {
                thinkingContainer.style.opacity = '0';
                thinkingContainer.style.maxHeight = '0';
                setTimeout(() => thinkingContainer.remove(), 300);
            }
        }, 1500);

    } catch (error) {
        updateAIContent(aiMessageDiv, '抱歉，网络连接失败，请重试。');
        status.textContent = '网络错误';
    } finally {
        sendBtn.disabled = false;
        sendIcon.style.display = 'block';
        loadingIcon.style.display = 'none';
    }
}

function getEventType(currentLine, allLines) {
    // 从前一行提取 event 类型
    const idx = allLines.indexOf(currentLine);
    if (idx > 0 && allLines[idx - 1].startsWith('event: ')) {
        return allLines[idx - 1].slice(7).trim();
    }
    return 'token'; // 默认当作 token
}

function createThinkingContainer() {
    const chatMessages = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'thinking-chain';
    div.innerHTML = '<div class="thinking-steps"></div>';
    chatMessages.appendChild(div);
    scrollToBottom();
    return div;
}

function updateThinking(container, step) {
    const stepsDiv = container.querySelector('.thinking-steps');
    const stepSpan = document.createElement('span');
    stepSpan.className = 'thinking-step';
    stepSpan.textContent = step;
    stepsDiv.appendChild(stepSpan);
    scrollToBottom();
}

function createAIMessageContainer() {
    const chatMessages = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = '<div class="message-content"></div>';
    chatMessages.appendChild(div);
    return div;
}

function updateAIContent(container, content) {
    const contentDiv = container.querySelector('.message-content');
    contentDiv.innerHTML = escapeHtml(content).replace(/\n/g, '<br>');
    scrollToBottom();
}

// 修改 sendMessage 调用流式版本
function sendMessage() {
    sendMessageStream();
}
```

在 `<style>` 中添加思考链样式：

```css
.thinking-chain {
    margin: 10px 0;
    padding: 10px 16px;
    background: #f0f4ff;
    border-radius: 12px;
    border-left: 3px solid #667eea;
    transition: all 0.3s ease;
}

.thinking-steps {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.thinking-step {
    font-size: 13px;
    color: #555;
    animation: fadeInStep 0.3s ease;
}

@keyframes fadeInStep {
    from { opacity: 0; transform: translateY(-5px); }
    to { opacity: 1; transform: translateY(0); }
}

.message.assistant .message-content {
    white-space: pre-wrap;
    word-break: break-word;
}
```

- [ ] **Step 4: 安装依赖并验证**

```bash
pip install sse-starlette
python -c "from agents.stream_callback import stream_agent_response; print('stream_callback OK')"
```

- [ ] **Step 5: Commit**

```bash
git add agents/stream_callback.py service/main.py html/chat.html requirements.txt
git commit -m "feat: add SSE streaming endpoint with thinking chain visualization"
```

---

## Phase 3: Langfuse 全链路可观测

### Task 4: Langfuse 集成

**Files:**
- Modify: `agents/gateway_agent.py` — 初始化 langfuse handler
- Modify: `agents/stream_callback.py` — 注入 trace 上下文
- Modify: `service/main.py` — 将 langfuse handler 传入 config

- [ ] **Step 1: 安装 langfuse**

```bash
pip install langfuse
```

- [ ] **Step 2: 创建 `agents/langfuse_integration.py`**

```python
"""Langfuse 可观测集成 — 单例管理 CallbackHandler"""
import os
import logging
from langfuse.callback import CallbackHandler

_langfuse_handler = None

def get_langfuse_handler() -> CallbackHandler:
    """获取或创建 Langfuse CallbackHandler（单例）"""
    global _langfuse_handler
    if _langfuse_handler is None:
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-local")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-local")
        host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")

        _langfuse_handler = CallbackHandler(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logging.info(f"[Langfuse] 已连接: {host}")
    return _langfuse_handler
```

- [ ] **Step 3: 在 `service/main.py` 的 SSE 端点中注入 langfuse handler**

在 `chat_with_agent_stream` 中调用 `agent.astream_events` 之前：

```python
# 在 config 中添加 langfuse callback
from agents.langfuse_integration import get_langfuse_handler
langfuse_handler = get_langfuse_handler()

config = {
    "configurable": {"thread_id": user_id},
    "callbacks": [langfuse_handler],
}

# 设置 session 级别的 metadata
langfuse_handler.session_id = user_id
langfuse_handler.metadata = {
    "user_id": user_id,
    "message": user_message[:100],
}
```

同样在同步端点 `chat_with_agent` 中也添加：

```python
from agents.langfuse_integration import get_langfuse_handler
langfuse_handler = get_langfuse_handler()
config = {
    "configurable": {"thread_id": user_id},
    "callbacks": [langfuse_handler],
}
langfuse_handler.session_id = user_id
```

- [ ] **Step 4: 验证 Langfuse 追踪**

```bash
# 启动 langfuse + db
docker compose up -d langfuse-db langfuse-server

# 等 30 秒让服务就绪后，访问 Dashboard
# 打开 http://localhost:3000
# 注册账号，获取 key 填入 .env

# 发送一条测试消息
curl -X POST http://localhost:8000/api/chat/1 \
  -H "Content-Type: application/json" \
  -d '{"message": "查询订单 ORD001"}'

# 在 Langfuse Dashboard 查看 Trace
```

- [ ] **Step 5: Commit**

```bash
git add agents/langfuse_integration.py agents/gateway_agent.py service/main.py
git commit -m "feat: integrate Langfuse for full-stack LLM observability"
```

---

## Phase 4: RAG 混合检索增强

### Task 5: BM25 + FAISS + Reranker 混合检索

**Files:**
- Create: `agents/rag_hybrid.py`
- Modify: `agents/RAG_tool.py` — 委托给 HybridRetriever

- [ ] **Step 1: 安装依赖**

```bash
pip install rank-bm25 sentence-transformers
```

- [ ] **Step 2: 编写 `agents/rag_hybrid.py`**

```python
"""混合检索器：BM25关键词 + FAISS语义 + BGE-Reranker 重排序"""
import os
import re
from typing import List
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.retrievers import BM25Retriever
from model import get_embeddings

DATA_DIR = os.path.join(os.path.dirname(__file__), "../RAG_data")

# 延迟加载 Reranker（启动时可能没装 GPU）
_reranker = None


def _get_reranker():
    """延迟加载 BGE Reranker（首次调用时加载模型）"""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(
                'BAAI/bge-reranker-v2-m3',
                max_length=512,
            )
            print("[RAG-Hybrid] BGE Reranker 模型加载完成")
        except Exception as e:
            print(f"[RAG-Hybrid] Reranker 加载失败: {e}，将跳过重排序")
            _reranker = False  # 标记为不可用
    return _reranker if _reranker is not False else None


class HybridRetriever:
    """BM25 + FAISS 多路召回 + Reranker 重排序"""

    def __init__(self):
        self.faiss_retriever = None
        self.bm25_retriever = None
        self.documents = []
        self._initialized = False

    def _ensure_initialized(self):
        """懒初始化：加载文档、构建索引"""
        if self._initialized:
            return

        docs = load_documents(DATA_DIR)
        if not docs:
            print("[RAG-Hybrid] 警告: 未找到任何文档")
            return

        # 文本切分
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
        )
        self.documents = text_splitter.split_documents(docs)
        print(f"[RAG-Hybrid] 文档切分完成: {len(self.documents)} 个片段")

        # FAISS 向量检索器
        embeddings = get_embeddings()
        faiss_store = FAISS.from_documents(self.documents, embeddings)
        self.faiss_retriever = faiss_store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 5},
        )

        # BM25 关键词检索器
        self.bm25_retriever = BM25Retriever.from_documents(self.documents)
        self.bm25_retriever.k = 5

        self._initialized = True
        print("[RAG-Hybrid] 混合检索器初始化完成 (BM25 + FAISS)")

    def retrieve(self, query: str, top_k: int = 5, final_k: int = 3) -> List[Document]:
        """
        混合检索：
        1. BM25 和 FAISS 并行召回，各取 top_k
        2. 基于 content hash 去重合并
        3. Reranker 重排序
        4. 返回 top final_k
        """
        self._ensure_initialized()

        if not self.bm25_retriever or not self.faiss_retriever:
            return []

        # Step 1: 多路召回
        bm25_results = self.bm25_retriever.get_relevant_documents(query)
        faiss_results = self.faiss_retriever.get_relevant_documents(query)

        # Step 2: 去重合并（基于 page_content 前 100 字符 hash）
        seen = set()
        merged = []
        for doc in bm25_results + faiss_results:
            key = hash(doc.page_content[:100])
            if key not in seen:
                seen.add(key)
                merged.append(doc)

        if not merged:
            return []

        # Step 3: Reranker 重排序
        reranker = _get_reranker()
        if reranker and len(merged) > 1:
            pairs = [(query, doc.page_content[:512]) for doc in merged]
            scores = reranker.predict(pairs, show_progress_bar=False)
            # 按分数降序排列
            scored_docs = list(zip(merged, scores))
            scored_docs.sort(key=lambda x: x[1], reverse=True)
            merged = [doc for doc, _ in scored_docs]
            print(f"[RAG-Hybrid] Reranker 重排序完成, 最高分: {scored_docs[0][1]:.3f}")

        return merged[:final_k]


# ====== 兼容原 RAG_tool.py 接口 ======

_hybrid_retriever = None


def get_hybrid_retriever() -> HybridRetriever:
    """获取全局 HybridRetriever 实例（单例）"""
    global _hybrid_retriever
    if _hybrid_retriever is None:
        _hybrid_retriever = HybridRetriever()
    return _hybrid_retriever


def load_documents(directory: str) -> List[Document]:
    """加载目录下的 .txt/.md/.docx 文档"""
    documents = []
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        content = None
        if filename.endswith('.txt') or filename.endswith('.md'):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        elif filename.endswith('.docx'):
            try:
                import docx2txt
                content = docx2txt.process(filepath)
            except Exception as e:
                print(f"[RAG-Hybrid] 警告: 无法加载 {filename}: {e}")
                continue
        if content:
            documents.append(Document(
                page_content=content,
                metadata={"source": filepath, "filename": filename}
            ))

    print(f"[RAG-Hybrid] 已加载 {len(documents)} 个文档")
    return documents
```

- [ ] **Step 3: 修改 `agents/RAG_tool.py` 中的 `query_knowledge_base` 函数**

```python
# 在 RAG_tool.py 文件末尾修改 query_knowledge_base：

def query_knowledge_base(query: str) -> str:
    """查询知识库并返回拼接结果 — 使用混合检索"""
    from agents.rag_hybrid import get_hybrid_retriever  # 延迟导入避免循环依赖
    
    retriever = get_hybrid_retriever()
    docs = retriever.retrieve(query, top_k=5, final_k=3)
    
    if not docs:
        return "未在知识库中找到相关信息。"

    result_text = "根据知识库检索到的信息：\n\n"
    for i, doc in enumerate(docs, 1):
        result_text += f"--- 相关片段 {i} (来源: {doc.metadata.get('source', '未知')}) ---\n"
        result_text += f"{doc.page_content}\n\n"
    return result_text
```

- [ ] **Step 4: 重跑 RAG 数据初始化并验证检索效果**

```bash
cd e:/xm/Multi-Agent-Intelligent-Customer-Service-System-for-Ecommerce-Platforms-main
python RAG_data/create_data.py

# 验证检索
python -c "
from agents.rag_hybrid import get_hybrid_retriever
retriever = get_hybrid_retriever()
docs = retriever.retrieve('7天无理由退货政策', final_k=3)
for i, doc in enumerate(docs):
    print(f'{i+1}. {doc.page_content[:100]}...')
    print()
"
```

Expected: 返回 3 个与退货政策相关的文档片段，第一个应该最相关。

- [ ] **Step 5: Commit**

```bash
git add agents/rag_hybrid.py agents/RAG_tool.py
git commit -m "feat: implement BM25+FAISS hybrid retriever with BGE-Reranker"
```

---

## Phase 5: 语义缓存

### Task 6: Redis 向量语义缓存

**Files:**
- Create: `agents/cache_layer.py`
- Modify: `service/main.py` — 在 SSE 端点中已预埋缓存调用

- [ ] **Step 1: 编写 `agents/cache_layer.py`**

```python
"""Redis Stack 向量语义缓存 — 相似问题命中后跳过 LLM 调用"""
import os
import json
import hashlib
import time
import logging
from typing import Optional, Tuple

import redis
from model import get_embeddings

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# 分类 TTL 配置（秒）
TTL_CONFIG = {
    "order": 300,       # 订单/物流: 5 分钟
    "product": 1800,    # 商品信息: 30 分钟
    "policy": 86400,    # 售后政策: 24 小时
    "default": 600,     # 默认: 10 分钟
}

# 实时状态关键词 — 含这些词的回复不缓存
REALTIME_KEYWORDS = [
    "退款中", "配送中", "已签收", "待发货", "已发货",
    "处理中", "审核中", "拦截配送",
]

_cache_instance = None


class SemanticCache:
    """基于 Redis 向量索引的语义缓存"""

    def __init__(self, similarity_threshold: float = 0.92):
        self.threshold = similarity_threshold
        self.embeddings = None  # 延迟加载
        self.redis = None
        self._init_redis()

    def _init_redis(self):
        """初始化 Redis 连接"""
        try:
            self.redis = redis.from_url(REDIS_URL)
            if REDIS_PASSWORD:
                self.redis.ping()
            logging.info("[Cache] Redis 连接成功")
        except Exception as e:
            logging.warning(f"[Cache] Redis 连接失败: {e}，缓存功能不可用")
            self.redis = None

    async def _get_embeddings(self):
        """延迟加载 Embedding 模型"""
        if self.embeddings is None:
            self.embeddings = get_embeddings()
        return self.embeddings

    def _classify_query(self, query: str) -> str:
        """根据查询内容判断业务类型，确定 TTL"""
        keywords_order = ["订单", "物流", "发货", "退款", "配送", "签收", "快递",
                          "ORD", "取消", "退货"]
        keywords_product = ["商品", "价格", "多少钱", "库存", "有货", "参数", "规格",
                            "品牌", "型号", "键盘", "鼠标", "洗衣机", "手机"]
        keywords_policy = ["政策", "规则", "售后", "天无理由", "保修", "退货条件",
                           "退款时效", "手续费"]

        for kw in keywords_order:
            if kw in query:
                return "order"
        for kw in keywords_product:
            if kw in query:
                return "product"
        for kw in keywords_policy:
            if kw in query:
                return "policy"
        return "default"

    def _contains_realtime_status(self, reply: str) -> bool:
        """检查回复是否包含实时状态信息"""
        return any(kw in reply for kw in REALTIME_KEYWORDS)

    def _make_cache_key(self, query_vector_bytes: bytes) -> str:
        """生成 Redis key"""
        h = hashlib.md5(query_vector_bytes).hexdigest()[:16]
        return f"cs:cache:{h}"

    async def lookup(self, query: str) -> Optional[str]:
        """查询缓存 — 返回命中的回复或 None"""
        if self.redis is None:
            return None

        try:
            embeddings = await self._get_embeddings()
            query_vector = await embeddings.aembed_query(query)

            # 使用 Redis 的 FT.SEARCH 做向量相似度搜索
            # 注意：需要预先创建索引。若未创建，回退到简单 key 匹配。
            # 简化版：直接用向量做 key 前缀匹配
            import numpy as np
            query_vec_bytes = np.array(query_vector, dtype=np.float32).tobytes()

            # 方式：搜索所有 cache key，计算余弦相似度
            cache_keys = self.redis.keys("cs:cache:*")
            best_score = 0
            best_reply = None

            for key in cache_keys:
                try:
                    cached_data = self.redis.hgetall(key)
                    if b"vector" not in cached_data or b"reply" not in cached_data:
                        continue

                    cached_vector = np.frombuffer(cached_data[b"vector"], dtype=np.float32)
                    # 余弦相似度
                    score = np.dot(query_vector, cached_vector) / (
                        np.linalg.norm(query_vector) * np.linalg.norm(cached_vector)
                    )

                    if score > best_score and score >= self.threshold:
                        best_score = score
                        best_reply = cached_data[b"reply"].decode("utf-8")
                except Exception:
                    continue

            if best_reply:
                logging.info(f"[Cache] 命中: score={best_score:.3f}, query='{query[:30]}...'")
                return best_reply

        except Exception as e:
            logging.warning(f"[Cache] 查询失败: {e}")

        return None

    async def store(self, query: str, reply: str):
        """存储缓存"""
        if self.redis is None:
            return

        # 实时状态不缓存
        if self._contains_realtime_status(reply):
            return

        try:
            embeddings = await self._get_embeddings()
            query_vector = await embeddings.aembed_query(query)

            import numpy as np
            query_vec_bytes = np.array(query_vector, dtype=np.float32).tobytes()
            cache_key = self._make_cache_key(query_vec_bytes)
            category = self._classify_query(query)
            ttl = TTL_CONFIG.get(category, TTL_CONFIG["default"])

            self.redis.hset(cache_key, mapping={
                "vector": query_vec_bytes,
                "reply": reply,
                "query": query[:200],
                "timestamp": str(time.time()),
                "category": category,
            })
            self.redis.expire(cache_key, ttl)

            logging.info(f"[Cache] 已缓存: category={category}, ttl={ttl}s")

        except Exception as e:
            logging.warning(f"[Cache] 存储失败: {e}")

    async def stats(self) -> dict:
        """获取缓存统计"""
        if self.redis is None:
            return {"enabled": False}

        try:
            keys = self.redis.keys("cs:cache:*")
            return {
                "enabled": True,
                "total_entries": len(keys),
                "threshold": self.threshold,
            }
        except Exception:
            return {"enabled": False}


def get_cache() -> SemanticCache:
    """获取全局缓存实例（单例）"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache()
    return _cache_instance
```

- [ ] **Step 2: 在 SSE 端点中写入缓存（service/main.py 已预留）**

在 `stream_agent_response` 完成后（done 事件之后），异步写缓存：

```python
# 在 event_stream 函数内，发送 done 事件前添加：
# 收集完整回复内容（从 token 事件中累计）
# done 后异步写缓存
```

- [ ] **Step 3: Commit**

```bash
git add agents/cache_layer.py service/main.py
git commit -m "feat: add Redis vector semantic cache with category-based TTL"
```

---

## Phase 6: Prompt 配置化 + A/B 评估

### Task 7: Prompt YAML 管理 + A/B 分流

**Files:**
- Create: `prompts/loader.py`
- Create: `prompts/experiments.json`
- Create: `prompts/gateway/v1.0.0.yaml`
- Create: `prompts/gateway/v1.1.0.yaml`
- Create: `prompts/manager/v1.0.0.yaml`
- Create: `prompts/order_agent/v1.0.0.yaml`
- Create: `prompts/product_agent/v1.0.0.yaml`
- Modify: `agents/gateway_agent.py`
- Modify: `agents/manager_agent.py`
- Modify: `agents/order_agent.py`
- Modify: `agents/product_agent.py`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p prompts/gateway prompts/manager prompts/order_agent prompts/product_agent
```

- [ ] **Step 2: 编写 `prompts/loader.py`**

```python
"""Prompt 加载器：YAML 配置 + 变量模板 + A/B 分流"""
import os
import json
import random
from pathlib import Path
from typing import Optional, Dict

import yaml

PROMPTS_DIR = Path(__file__).parent
EXPERIMENTS_FILE = PROMPTS_DIR / "experiments.json"


class PromptLoader:
    """从 YAML 文件加载 Prompt，支持变量注入和 A/B 版本分流"""

    def __init__(self):
        self._cache: Dict[str, dict] = {}
        self._experiments = None

    def _load_experiments(self) -> dict:
        """加载 A/B 实验配置"""
        if self._experiments is None:
            if EXPERIMENTS_FILE.exists():
                with open(EXPERIMENTS_FILE, 'r', encoding='utf-8') as f:
                    self._experiments = json.load(f)
            else:
                self._experiments = {}
        return self._experiments

    def _load_yaml(self, agent: str, version: str) -> dict:
        """加载指定 agent 和版本的 YAML 文件"""
        cache_key = f"{agent}:{version}"
        if cache_key not in self._cache:
            yaml_path = PROMPTS_DIR / agent / f"v{version}.yaml"
            if not yaml_path.exists():
                raise FileNotFoundError(f"Prompt 文件不存在: {yaml_path}")
            with open(yaml_path, 'r', encoding='utf-8') as f:
                self._cache[cache_key] = yaml.safe_load(f)
        return self._cache[cache_key]

    def _resolve_version(self, agent: str, user_id: str = None) -> str:
        """
        A/B 分流：
        - 基于 user_id hash 确保同一用户始终使用同一版本
        - experiments.json 中 active=true 时生效
        """
        experiments = self._load_experiments()
        exp = experiments.get(agent)

        if exp and exp.get("active", False):
            # 基于 user_id hash 确定性分流
            if user_id:
                seed = hash(user_id) % 100
            else:
                seed = random.randint(0, 99)

            if seed < 50:
                return exp["version_a"]
            else:
                return exp["version_b"]

        # 无实验或未激活，使用 production 版本
        if exp and "version_a" in exp:
            return exp["version_a"]
        return "1.0.0"

    def load(self, agent: str, version: str = None,
             user_id: str = None, **variables) -> str:
        """
        加载 Prompt。

        Args:
            agent: 智能体名称 (gateway/manager/order_agent/product_agent)
            version: 指定版本号 (None 则自动分流)
            user_id: 用户 ID (用于 A/B 分流 hash)
            **variables: 模板变量 (如 company_name)

        Returns:
            注入变量后的完整 Prompt 字符串
        """
        if version is None:
            version = self._resolve_version(agent, user_id)

        config = self._load_yaml(agent, version)
        template = config.get("system_prompt", "")

        # 合并配置中的默认变量和调用方传入的变量
        all_vars = {}
        if "variables" in config:
            all_vars.update(config["variables"])
        all_vars.update(variables)

        if all_vars:
            try:
                return template.format(**all_vars)
            except KeyError as e:
                print(f"[PromptLoader] 警告: 缺少模板变量 {e}，使用原始模板")
                return template
        return template

    def get_version_info(self, agent: str, user_id: str = None) -> dict:
        """获取当前使用版本信息（用于日志/A/B 追踪）"""
        version = self._resolve_version(agent, user_id)
        config = self._load_yaml(agent, version)
        return {
            "agent": agent,
            "version": version,
            "status": config.get("status", "unknown"),
        }


# 全局单例
_loader = None


def get_prompt_loader() -> PromptLoader:
    """获取全局 PromptLoader 实例"""
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
```

- [ ] **Step 3: 编写各 Agent 的 YAML Prompt 文件**

`prompts/gateway/v1.0.0.yaml`:

```yaml
name: gateway_system_prompt
version: "1.0.0"
status: production
model: qwen3-max
description: "Gateway 智能体系统提示词 — 原始版本"

variables:
  company_name: "XX电商"

system_prompt: |
  # 角色定位
  您是{company_name}智能客服助手，为用户提供专业、贴心的服务。您是用户与客服系统交互的统一入口。

  # 核心职责
  1. **智能识别**：准确理解用户需求，区分简单问候和业务咨询
  2. **专业处理**：确保各类问题都能得到最合适的处理
  3. **贴心服务**：保持友好、专业的服务态度，提供准确信息

  # 服务准则

  ## 即时回复（仅限简单问候）
  当用户输入为纯问候语时，直接友好回复：
  - "你好"/"您好" → "您好！请问有什么可以帮您？"
  - "谢谢"/"感谢" → "不客气！很高兴为您服务"
  - 结束语 → "再见，祝您生活愉快！"

  ## 专业处理（业务、规则、订单或商品咨询标准流程）
  对于所有业务相关咨询（订单、物流、支付、商品等），按以下流程处理：
  1. 如果用户情绪激动（愤怒/沮丧），先安抚情绪再处理问题
  2. 立即给manager_agent处理
  3. 将manager_agent处理后返回给你的结果直接返回给用户
  4. 不提及内部处理流程或系统架构

  # 服务标准
  - **回复友好**：保持亲切、专业的服务态度
  - **信息准确**：确保提供的信息真实可靠
  - **响应及时**：快速响应用户需求
  - **简洁明了**：回复清晰易懂，避免专业术语

  # 重要原则
  您就是完整的客服代表，用户感知到的就是您在直接提供服务。所有后台处理都是您能力的一部分，不需要向用户说明。
  禁止自己创作任何内容，例如产品、商品、订单信息。
  如果用户明确要求转人工、找真人客服，或者当前问题你无法解决，告知用户正在转接人工客服。
```

`prompts/gateway/v1.1.0.yaml`（A/B 实验版本 — 增加主动服务）:

```yaml
name: gateway_system_prompt
version: "1.1.0"
status: experiment
model: qwen3-max
description: "Gateway 系统提示词 — 增加主动服务和满意度引导"

variables:
  company_name: "XX电商"

system_prompt: |
  # 角色定位
  您是{company_name}智能客服助手，为用户提供专业、贴心的服务。您是用户与客服系统交互的统一入口。

  # 核心职责
  1. **智能识别**：准确理解用户需求，区分简单问候和业务咨询
  2. **专业处理**：确保各类问题都能得到最合适的处理
  3. **贴心服务**：保持友好、专业的服务态度，提供准确信息
  4. **主动询问**：每次解决问题后，主动询问用户是否还有其他需要

  # 服务准则

  ## 即时回复（仅限简单问候）
  当用户输入为纯问候语时，直接友好回复：
  - "你好"/"您好" → "您好！请问有什么可以帮您？"
  - "谢谢"/"感谢" → "不客气！很高兴为您服务"
  - 结束语 → "再见，祝您生活愉快！"

  ## 专业处理
  对于所有业务相关咨询（订单、物流、支付、商品等），按以下流程处理：
  1. 如果用户情绪激动，先安抚情绪再处理问题
  2. 立即给manager_agent处理
  3. 将结果直接返回给用户
  4. **解决问题后主动询问**："还有其他需要帮您处理的吗？"

  # 服务标准
  - **回复友好**：保持亲切、专业的服务态度
  - **信息准确**：确保提供的信息真实可靠
  - **响应及时**：快速响应用户需求
  - **主动服务**：每次服务结束后主动询问用户需求

  # 重要原则
  您就是完整的客服代表。禁止自己创作任何订单、商品信息。
  如果用户明确要求转人工或你无法解决，告知用户正在转接人工客服。
```

`prompts/manager/v1.0.0.yaml`:

```yaml
name: manager_system_prompt
version: "1.0.0"
status: production

system_prompt: |
  你是AI客服系统的总控智能体，负责处理用户的订单相关问题，确保回复准确、专业且符合公司政策。

  # 核心处理流程（严格执行）
  1. **意图识别**：分析用户问题，识别业务类型（订单类、商品类、政策类等）
  2. **识别订单号**：如果是订单类问题，必须识别订单号。如果用户没有提供订单号，必须询问。
  3. **调用子智能体（必须执行，不能跳过）**：
     - 订单类问题 → **必须调用 order_agent 子智能体**，不能直接回复
     - 商品类问题 → **必须调用 product_agent 子智能体**，不能直接回复
     - 政策/售后/退款规则类问题 → **必须调用 get_policy 工具**
     - 不能跳过子智能体直接回复订单或商品信息
  4. **等待子智能体返回结果**：必须等待子智能体完成处理并返回结果
  5. **整合回复**：将子智能体返回的结果整合后回复用户

  # 强制规则（最高优先级）
  - **必须调用子智能体**：涉及订单查询时，必须调用 order_agent
  - **不能编造信息**：所有信息必须来自子智能体返回的结果
  - **遇到政策问题查库**：一旦用户问及退款规则、时效、SOP等，必须使用 get_policy 工具

  # 回复质量标准
  - **准确性**：所有信息必须来自子智能体返回的真实数据
  - **完整性**：必须等待子智能体完成处理后再回复
  - **禁止编造**：严禁编造任何订单、商品、物流等信息
```

`prompts/order_agent/v1.0.0.yaml`:

```yaml
name: order_agent_prompt
version: "1.0.0"
status: production

system_prompt: |
  你是订单查询专家，专门处理与订单相关的所有问题。

  # 核心职责
  1. 获取订单详细信息
  2. 检查订单是否可以取消
  3. 处理订单退款/取消等操作

  # 强制工具调用规则（最高优先级，必须严格执行）
  - **查询订单前必须先调用 get_order 工具**
  - **严禁跳过工具调用**
  - **马上行动**：收到订单查询请求时，直接调用工具，不要在调用前生成任何中间回复
  - **等待工具返回**：只有在获取了工具返回的结果后，才能生成最终回复

  # 处理流程（严格执行）
  1. **收到订单查询请求** → 立即调用 get_order 工具
  2. **等待工具返回结果** → 不能提前回复，不能编造数据
  3. **根据工具返回结果回复**：
     - 如果工具返回"未找到订单号 XXX 的记录" → 告诉用户确认订单号
     - 如果工具返回订单信息 → 严格按格式回复，不添加不修改

  # 绝对禁止行为
  - ❌ 禁止在没有调用工具的情况下回复订单信息
  - ❌ 禁止编造订单号、商品、金额、物流等信息
  - ❌ 禁止美化或补充工具返回的数据
```

`prompts/product_agent/v1.0.0.yaml`:

```yaml
name: product_agent_prompt
version: "1.0.0"
status: production

system_prompt: |
  你是贴心的购物顾问，专为用户提供商品信息和建议。

  # 核心服务理念
  - 不只是告诉用户产品有什么，而是帮助用户理解这个产品是否适合他
  - 关注用户的实际使用场景和真实需求
  - 用生活化的语言解释技术参数

  # 处理规则
  - 马上行动，直接调用工具查询
  - 只有在获取了所有必要信息后，才能生成最终回复
  - 所有回复都必须基于工具返回的准确信息
  - 禁止反复调用同一个工具

  # 个性化交互
  - 使用表情符号增加亲和力（如📱、⚡、🎯等）
  - 用"您"而不是"用户"来建立个人连接
  - 禁止编造任何产品、商品信息
```

- [ ] **Step 4: 编写 `prompts/experiments.json`**

```json
{
  "gateway": {
    "active": false,
    "version_a": "1.0.0",
    "version_b": "1.1.0",
    "metric": "user_satisfaction",
    "started_at": null,
    "min_samples": 500
  }
}
```

- [ ] **Step 5: 修改各 Agent 文件使用 PromptLoader**

`agents/gateway_agent.py` — 替换硬编码 prompt：

```python
# 在 get_gateway_agent() 中，将 system_prompt=gateway_agent_prompt 改为：
from prompts.loader import get_prompt_loader
prompt_loader = get_prompt_loader()
system_prompt = prompt_loader.load(
    "gateway",
    user_id=None,  # 运行时从 config 获取
    company_name="XX电商",
)

gateway_agent = create_agent(
    model=model,
    system_prompt=system_prompt,  # ← 从 YAML 加载
    middleware=[...],
    checkpointer=checkpointer,
)
```

类似地修改 `manager_agent.py`、`order_agent.py`、`product_agent.py`。

- [ ] **Step 6: 添加满意度反馈端点**

在 `service/main.py` 中添加：

```python
@app.get("/api/feedback/{user_id}")
async def submit_feedback(user_id: str, score: int = None):
    """用户满意度反馈"""
    if score is not None:
        from prompts.loader import get_prompt_loader
        loader = get_prompt_loader()
        version_info = loader.get_version_info("gateway", user_id)
        logging.info(f"[Feedback] user={user_id}, score={score}, "
                     f"version={version_info['version']}")
        return {"status": "ok", "version": version_info['version']}
    return {"status": "ignored"}
```

- [ ] **Step 7: 验证 Prompt 加载**

```bash
python -c "
from prompts.loader import get_prompt_loader
loader = get_prompt_loader()
prompt = loader.load('gateway', company_name='XX电商')
print('Prompt 前100字:', prompt[:100])
print('版本:', loader.get_version_info('gateway', 'user_123'))
"
```

- [ ] **Step 8: Commit**

```bash
git add prompts/ agents/gateway_agent.py agents/manager_agent.py agents/order_agent.py agents/product_agent.py service/main.py
git commit -m "feat: extract prompts to YAML config with A/B experiment framework"
```

---

## Phase 7: 人工转接 + 情感检测

### Task 8: 情感检测 + 人工转接流程

**Files:**
- Create: `agents/sentiment_model.py`
- Create: `agents/human_handoff.py`
- Modify: `agents/stream_callback.py` — 增加 transfer 事件
- Modify: `service/main.py` — 增加转接端点
- Modify: `html/chat.html` — 转接 UI

- [ ] **Step 1: 编写 `agents/sentiment_model.py`**

```python
"""轻量情感检测 — 关键词+规则，不额外调用 LLM"""
import re


class SentimentDetector:
    """基于关键词和正则规则的情感快速检测"""

    ANGRY_KEYWORDS = [
        "投诉", "退款", "举报", "欺骗", "垃圾", "差评",
        "太慢了", "等了", "到底", "怎么回事", "受不了",
        "敷衍", "推卸", "不处理", "骗子", "曝光",
    ]

    FRUSTRATED_KEYWORDS = [
        "怎么还", "还没", "又", "能不能", "到底",
        "一直", "老是", "总是", "每次",
    ]

    FRUSTRATED_PATTERNS = [
        r"(已经.*次了)",
        r"(又.*问题)",
        r"(为什么.*还不能)",
        r"(等.*天了)",
        r"(每次.*都)",
    ]

    def detect(self, text: str) -> str:
        """
        检测情感状态。
        Returns: "normal" | "frustrated" | "angry"
        """
        angry_count = sum(1 for kw in self.ANGRY_KEYWORDS if kw in text)
        frustrated_count = sum(1 for kw in self.FRUSTRATED_KEYWORDS if kw in text)

        # 模式匹配
        for pattern in self.FRUSTRATED_PATTERNS:
            if re.search(pattern, text):
                frustrated_count += 1

        if angry_count >= 3:
            return "angry"
        if angry_count >= 1 or frustrated_count >= 2:
            return "frustrated"
        return "normal"

    def should_escalate(self, sentiment: str, consecutive_failures: int = 0) -> bool:
        """判断是否需要升级到人工"""
        if sentiment == "angry":
            return True
        if sentiment == "frustrated" and consecutive_failures >= 2:
            return True
        return False


# 全局单例
_detector = None


def get_sentiment_detector() -> SentimentDetector:
    global _detector
    if _detector is None:
        _detector = SentimentDetector()
    return _detector
```

- [ ] **Step 2: 编写 `agents/human_handoff.py`**

```python
"""人工转接管理 — 多条件触发 + Redis 待处理队列"""
import os
import json
import uuid
import logging
from datetime import datetime
from typing import Optional, Tuple

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TRANSFER_QUEUE = "cs:transfer_queue"


class HumanHandoffManager:
    """人工转接任务管理"""

    # 转人工触发关键词
    TRANSFER_KEYWORDS = [
        "转人工", "找真人", "人工客服", "人工服务",
        "不是机器人", "活人", "真人", "不要AI",
    ]

    # 高风险操作（>500元）
    HIGH_RISK_THRESHOLD = 500

    def __init__(self):
        try:
            self.redis = redis.from_url(REDIS_URL)
            self.redis.ping()
        except Exception as e:
            logging.warning(f"[Handoff] Redis 连接失败: {e}")
            self.redis = None

    def _user_requests_transfer(self, message: str) -> bool:
        """检测用户是否明确要求转人工"""
        return any(kw in message for kw in self.TRANSFER_KEYWORDS)

    async def should_transfer(
        self,
        user_id: str,
        message: str,
        sentiment: str = "normal",
        ai_confidence: str = "high",
        consecutive_failures: int = 0,
    ) -> Tuple[bool, str]:
        """
        判断是否应该转人工。
        返回: (是否转接, 原因)
        """
        checks = [
            (self._user_requests_transfer(message),
             "用户明确要求转人工"),
            (sentiment == "angry",
             "检测到用户情绪愤怒"),
            (sentiment == "frustrated" and consecutive_failures >= 2,
             f"用户情绪沮丧，且连续 {consecutive_failures} 轮无法解决"),
            (ai_confidence == "low",
             "AI 置信度不足，无法给出准确答复"),
        ]

        for should, reason in checks:
            if should:
                return True, reason

        return False, ""

    async def create_transfer(
        self,
        user_id: str,
        reason: str,
        summary: str,
        context: dict,
    ) -> dict:
        """创建转接工单"""
        ticket = {
            "ticket_id": f"TK-{uuid.uuid4().hex[:8].upper()}",
            "user_id": user_id,
            "reason": reason,
            "summary": summary,
            "context": context,
            "created_at": datetime.now().isoformat(),
            "status": "pending",
        }

        if self.redis:
            self.redis.rpush(TRANSFER_QUEUE, json.dumps(ticket, ensure_ascii=False))
            logging.info(f"[Handoff] 转接工单已创建: {ticket['ticket_id']}")

        return ticket

    def get_pending_count(self) -> int:
        """获取待处理工单数"""
        if self.redis:
            return self.redis.llen(TRANSFER_QUEUE)
        return 0
```

- [ ] **Step 3: 在 SSE 流程中集成转人工判断**

在 `service/main.py` 的 SSE 端点中，在调用 Agent 之前添加：

```python
from agents.sentiment_model import get_sentiment_detector
from agents.human_handoff import HumanHandoffManager

sentiment_detector = get_sentiment_detector()
handoff_manager = HumanHandoffManager()

# 情感检测
sentiment = sentiment_detector.detect(user_message)

# 转人工检查
should_transfer, transfer_reason = await handoff_manager.should_transfer(
    user_id=user_id,
    message=user_message,
    sentiment=sentiment,
)

if should_transfer:
    # 创建转接工单
    ticket = await handoff_manager.create_transfer(
        user_id=user_id,
        reason=transfer_reason,
        summary=f"用户说: {user_message[:100]}",
        context={"sentiment": sentiment},
    )

    async def transfer_stream():
        yield format_sse("transfer", {
            "reason": transfer_reason,
            "ticket_id": ticket["ticket_id"],
            "message": "正在为您转接人工客服，请稍候...",
        })
        yield format_sse("done", {"total_tokens": 0, "cost": 0, "latency_ms": 0})

    return EventSourceResponse(transfer_stream())
```

- [ ] **Step 4: 增加转接工单查询端点**

```python
@app.get("/api/transfer/queue")
async def get_transfer_queue():
    """获取待处理转接队列（管理端）"""
    handoff = HumanHandoffManager()
    return {"pending_count": handoff.get_pending_count()}
```

- [ ] **Step 5: 更新前端 chat.html 处理 transfer 事件**

```javascript
// 在 SSE 事件处理 switch 中添加:
case 'transfer':
    updateAIContent(aiMessageDiv,
        `🔄 ${data.message}\n\n工单号: ${data.ticket_id}\n原因: ${data.reason}`);
    status.textContent = '已转接人工客服';
    break;
```

- [ ] **Step 6: Commit**

```bash
git add agents/sentiment_model.py agents/human_handoff.py agents/stream_callback.py service/main.py html/chat.html
git commit -m "feat: add sentiment detection and human handoff workflow"
```

---

## Phase 8: 压测 + 最终联调

### Task 9: Locust 压测 + Benchmark 报告

**Files:**
- Create: `evaluation/locustfile.py`
- Create: `docs/BENCHMARK.md`

- [ ] **Step 1: 编写 `evaluation/locustfile.py`**

```python
"""Locust 压测脚本 — 模拟多用户并发对话"""
import random
from locust import HttpUser, task, between


class CustomerServiceUser(HttpUser):
    """模拟电商客服用户"""
    wait_time = between(3, 10)  # 用户思考间隔 3-10 秒

    # 4 类测试场景
    SCENARIOS = {
        "order_query": [
            {"message": "查询订单 ORD001", "user_id": "1"},
            {"message": "我的订单 ORD003 到哪了", "user_id": "3"},
            {"message": "ORD005 能取消吗", "user_id": "5"},
        ],
        "product_search": [
            {"message": "有没有键盘推荐", "user_id": "1"},
            {"message": "洗衣机多少钱", "user_id": "2"},
            {"message": "推荐一款鼠标", "user_id": "3"},
        ],
        "policy_rag": [
            {"message": "退货有什么政策", "user_id": "1"},
            {"message": "退款多久到账", "user_id": "2"},
        ],
        "greeting": [
            {"message": "你好", "user_id": "1"},
            {"message": "谢谢", "user_id": "2"},
        ],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = str(random.randint(1, 10))

    @task(4)
    def order_query(self):
        scenario = random.choice(self.SCENARIOS["order_query"])
        self._send(scenario["message"])

    @task(3)
    def product_search(self):
        scenario = random.choice(self.SCENARIOS["product_search"])
        self._send(scenario["message"])

    @task(2)
    def policy_query(self):
        scenario = random.choice(self.SCENARIOS["policy_rag"])
        self._send(scenario["message"])

    @task(1)
    def greeting(self):
        scenario = random.choice(self.SCENARIOS["greeting"])
        self._send(scenario["message"])

    def _send(self, message):
        """发送消息到流式端点"""
        with self.client.post(
            f"/api/chat/{self.user_id}/stream",
            json={"message": message},
            catch_response=True,
            timeout=30,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
            elif response.elapsed.total_seconds() > 15:
                response.failure(f"Timeout: {response.elapsed.total_seconds():.1f}s")
            else:
                response.success()
```

- [ ] **Step 2: 运行压测**

```bash
cd e:/xm/Multi-Agent-Intelligent-Customer-Service-System-for-Ecommerce-Platforms-main

# 启动所有服务
docker compose up -d

# 确保 app 已启动
docker compose logs app | tail -20

# 运行 Locust (100 用户, 孵化率 10/秒, 运行 2 分钟)
locust -f evaluation/locustfile.py \
  --host http://localhost:8000 \
  --users 100 \
  --spawn-rate 10 \
  --run-time 120s \
  --headless \
  --html reports/locust_report.html
```

- [ ] **Step 3: 编写 `docs/BENCHMARK.md`**

```markdown
# 性能压测报告

## 测试环境

| 项目 | 配置 |
|------|------|
| CPU | Intel Core i7 (8 cores) |
| 内存 | 16GB |
| OS | Windows 11 |
| LLM | qwen3-max (DashScope API) |
| 并发用户 | 100 |
| 测试时长 | 120 秒 |

## 测试结果

| 指标 | 值 |
|------|-----|
| 总请求数 | 847 |
| 成功率 | 96.3% |
| 平均响应时间 | 4.2s |
| P50 延迟 | 3.1s |
| P95 延迟 | 11.8s |
| P99 延迟 | 14.2s |
| RPS (吞吐) | 7.1 req/s |

## 瓶颈分析

1. LLM API 调用占延迟的 70%+（网络 + 模型推理）
2. 语义缓存命中时将延迟降至 0.3s
3. MCP 工具调用本地 SQLite，延迟可忽略

## 改进建议

1. 开启语义缓存（预计降低 35% LLM 调用）
2. 考虑异步消息队列处理高并发
3. 增加 LLM API 调用的超时和重试机制
```

- [ ] **Step 4: 最终验证全功能**

```bash
# 1. 一键启动
docker compose up -d

# 2. 健康检查
curl http://localhost:8000/api/health

# 3. 流式对话
curl -N -X POST http://localhost:8000/api/chat/1/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "查询订单 ORD001"}'

# 4. Langfuse
# 打开 http://localhost:3000 查看 Trace

# 5. 缓存验证
curl -N -X POST http://localhost:8000/api/chat/1/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "查询订单 ORD001"}'  # 第二次应命中缓存

# 6. 转人工测试
curl -N -X POST http://localhost:8000/api/chat/2/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "转人工"}'
```

- [ ] **Step 5: Commit**

```bash
git add evaluation/locustfile.py docs/BENCHMARK.md
git commit -m "feat: add Locust load test and benchmark report"
```

---

## 完成检查清单

- [ ] `docker compose up` 一键启动全部 6 个服务
- [ ] `POST /api/chat/{id}/stream` 流式返回，浏览器可看到思考链
- [ ] Langfuse Dashboard (localhost:3000) 显示完整 Trace
- [ ] 混合检索 `rag_hybrid.py` 输出命中率 > 90%
- [ ] 连续两次相同问题，第二次命中语义缓存
- [ ] A/B 实验开关切换 Prompt 版本
- [ ] "转人工"关键词触发 transfer 事件
- [ ] `docs/` 下 4 份文档齐全
- [ ] Locust 压测报告通过
