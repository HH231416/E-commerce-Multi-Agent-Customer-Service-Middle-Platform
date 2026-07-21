# 系统架构文档

## 概述

电商多智能体智能客服平台（E-Commerce Multi-Agent Intelligent Customer Service Platform）是一套基于 **LangGraph + MCP 协议** 的智能客服平台。系统采用 **三层智能体架构**（Gateway - Manager - SubAgents），通过层级化任务分发与领域专业化分工，实现对订单查询、商品推荐、售后政策解答等复杂电商场景的高效处理。

核心技术特征：

- **层级化智能体编排**：Gateway Agent 负责会话入口与上下文管理，Manager Agent 负责意图识别与子智能体调度，Domain Sub-Agents 专注特定领域业务逻辑
- **MCP 标准化工具协议**：通过 Model Context Protocol 实现 LLM 与数据源的解耦，支持工具调用的标准化与透明化上下文注入
- **RAG 知识检索增强**：基于 Milvus 持久化向量数据库，实现公司政策、SOP 手册的结构化与非结构化语义检索
- **全链路异步架构**：FastAPI + Asyncio + AsyncRedisSaver，支持分布式状态持久化与高并发处理

---

## 技术选型

| 组件 | 选型 | 决策理由 |
|------|------|---------|
| LLM 框架 | LangGraph | 支持条件路由、状态持久化（Checkpoint）、流式事件推送，天然适配多智能体编排场景 |
| Agent 框架 | DeepAgents + LangChain | 子智能体编排能力、中间件机制（Summarization / TodoList / Filesystem），通过 `CompiledSubAgent` 实现层级调用 |
| 工具协议 | MCP (Model Context Protocol) | 标准化工具接口，通过 stdio 传输协议解耦 LLM 与数据源；支持 `langchain_mcp_adapters` 客户端自动发现工具 |
| API 服务 | FastAPI | 高性能异步框架，原生 SSE 支持，自动生成 OpenAPI 文档，适合流式对话场景 |
| 业务数据库 | SQLite (dev) -> PostgreSQL (prod) | 开发环境零配置即可启动；生产环境通过 SqlAlchemy 无缝迁移至 PostgreSQL |
| 向量数据库 | Milvus | 持久化向量索引，支持混合检索策略（BM25 + 向量）；v2.4.x 版本稳定可靠 |
| 缓存 | Redis Stack | 同时满足两类需求：(1) `AsyncRedisSaver` 基于 Redis 实现跨进程对话状态持久化 (2) Redis Stack 内置向量相似度搜索与 JSON 支持 |
| 可观测 | Langfuse | LLM 原生 Trace 追踪，支持 token 用量统计、成本分析、实验对比；开源自建免外部依赖 |
| LLM | DashScope (Qwen 系列) | 阿里云百炼平台，OpenAI 兼容接口，涵盖通用模型（qwen-plus/qwen-max）、路由模型（qwen3-max）、Embedding 模型（text-embedding-v3） |
| 容器化 | Docker Compose | 统一编排 Redis Stack / Milvus / Etcd / Minio / Langfuse / App 六大服务 |

---

## 智能体架构

系统采用 **Gateway -> Manager -> SubAgents** 三层架构，每层职责明确、边界清晰。

### Gateway Agent

**职责**：会话入口与门面

- 接收来自 FastAPI 的所有用户请求，作为系统唯一对外的智能体
- 管理会话生命周期：通过 `AsyncRedisSaver` 持久化对话状态，支持断点续传
- 消息摘要：当对话历史超过 30 条时，`SummarizationMiddleware` 自动触发摘要，保留最近 30 条消息
- 情感检测与转人工判断：通过系统提示词约束，识别超出能力范围的请求并友好引导
- 路由决策：将业务类请求委派给 Manager Agent 处理（通过 `SubAgentMiddleware`）

**中间件链**（按执行顺序）：

| 中间件 | 说明 |
|--------|------|
| `SubAgentMiddleware` | 注册 manager-agent 为子智能体，Gateway 可将任务委派给 Manager |
| `SummarizationMiddleware` | 触发条件：消息数 >= 30，保留策略：最近 30 条 |
| `TodoListMiddleware` | 跟踪多步任务的执行状态 |
| `FilesystemMiddleware` | 提供文件系统读写能力（调试/日志） |

### Manager Agent

**职责**：业务总控与调度中枢

- 意图识别：分析用户输入，判断属于订单类、商品类、政策类或闲聊
- 任务拆解：对复杂请求进行分解，按业务类型分派给对应子智能体
- 子智能体调度：通过 `create_deep_agent` 注册 Order Agent 和 Product Agent
- RAG 调用：通过 `get_policy` 工具调用 Milvus 知识库检索售后政策、SOP 手册
- 结果整合：等待子智能体返回结果后整合输出，确保不编造信息

**子智能体注册**：

| 子智能体 | 名称 | 职责 |
|---------|------|------|
| Order Agent | `order_agent` | 订单查询、可取消检查、退款/取消操作 |
| Product Agent | `product_agent` | 商品查询、关键词搜索、基本信息获取 |

**工具注册**：

| 工具 | 说明 |
|------|------|
| `get_policy` | RAG 知识库检索工具，搜索售后服务协议、SOP 手册、常见问题 |

### Order Agent

**职责**：订单领域专家

- 通过 MCP 协议连接订单数据库（SQLite），调用以下工具：

| MCP 工具 | 功能 |
|---------|------|
| `get_order` | 获取订单详情（含用户权限校验） |
| `check_cancelable` | 检查订单是否可取消/退款 |
| `refund_order` | 提交退款申请，更新订单状态 |

- 利用 `mcp_wrapper` 自动从 `RunnableConfig` 中注入 `user_id`，对 LLM 隐藏该参数但运行时保证数据隔离
- 系统提示词严格约束：必须先调用工具获取真实数据，禁止编造订单信息

### Product Agent

**职责**：商品领域专家

- 通过 MCP 协议连接商品数据库（SQLite），调用以下工具：

| MCP 工具 | 功能 |
|---------|------|
| `get_product_info` | 获取单个商品完整信息（名称、价格、库存、规格、品牌等） |
| `search_products` | 按关键词/分类/最高价格搜索商品列表 |
| `get_product_basic_info` | 获取商品基本信息和库存状态 |

- 系统提示词定位于"购物顾问"，强调需求匹配而非参数罗列

---

## 数据流

```
                        _..._
                     .-'       '-.
                    /   Redis      \
                   |   (Checkpoint) |
                    \_           _/
                      '-.......-'

  +------+    HTTP     +-------------+     SSE      +----------+
  | User | ----------> |  FastAPI    | -----------> | Gateway  |
  |      | <---------- |  (port:8000)| <----------- |  Agent   |
  +------+    JSON     +-------------+    JSON      +----------+
                             |                            |
                             |  POST /api/chat/{user_id}  | SubAgentMiddleware
                             |  POST /api/init_chat       | (handoff)
                             |  GET  /api/chat_history    |
                             v                            v
                      +-----+--------+           +----------------+
                      | AsyncRedisSaver|          |  Manager Agent |
                      | (Redis Stack)  |          |  (delegation)  |
                      +----------------+          +----------------+
                                                       |        |
                                           CompiledSubAgent   Tool
                                                       |        |
                                          +------------+ |  +-----------+
                                          v               |  v
                                    +---------+          |  +-------------+
                                    | Order   |          +->| get_policy  |
                                    | Agent   |             | (RAG Tool)  |
                                    +---------+             +-------------+
                                          |                       |
                                    MCP stdio                Milvus
                                    (langchain_mcp)          (Vector DB)
                                          |                       |
                                          v                       v
                                    +---------+           +-------------+
                                    |orders.db|           | RAG_data/   |
                                    |(SQLite) |           | *.md/*.docx |
                                    +---------+           +-------------+

                                          v
                                    +---------+
                                    | Product |
                                    | Agent   |
                                    +---------+
                                          |
                                    MCP stdio
                                          |
                                          v
                                    +-----------+
                                    |products.db|
                                    |(SQLite)   |
                                    +-----------+
```

**数据流步骤说明**：

1. **用户发送消息** -> FastAPI 接收 HTTP 请求，路由至 `/api/chat/{user_id}`
2. **状态恢复** -> Gateway Agent 通过 `AsyncRedisSaver` 从 Redis 恢复该 `user_id` 的对话历史
3. **自愈检查** -> 检测是否存在未完成的工具调用（上次崩溃残留），若有则注入伪造 ToolMessage 修复状态
4. **Gateway 决策** -> 简单问候直接回复；业务请求通过 `SubAgentMiddleware` 委派给 Manager Agent
5. **Manager 路由** -> 意图识别后分派：(a) Order Agent 处理订单 (b) Product Agent 处理商品 (c) `get_policy` 工具查 RAG 知识库
6. **MCP 工具调用** -> Order/Product Agent 通过 stdin/stdout 与 MCP Server 通信，SQLite 执行查询
7. **结果回流** -> 子智能体 -> Manager 整合 -> Gateway 包装 -> FastAPI JSON Response -> 用户
8. **状态保存** -> `AsyncRedisSaver` 在每个 checkpoint 自动将消息状态持久化到 Redis

---

## 关键设计决策 (ADR)

### ADR-001: MCP 协议而非直接数据库访问

**决策**：采用 MCP (Model Context Protocol) 标准化工具协议，而非在 Agent 中直接通过 SQL 或 ORM 访问数据库。

**理由**：

- **解耦 LLM 与数据源**：LLM 只需知道工具签名（名称 + 描述 + 参数 Schema），无需感知底层存储
- **安全隔离**：MCP Server 运行在独立进程，可实现权限校验（如 `user_id` 校验）集中在服务端
- **标准化接口**：MCP 将工具定义为标准 Schema，与 LangChain 工具系统无缝集成
- **可替换性**：更换数据库（如 SQLite -> PostgreSQL）只需修改 MCP Server，Agent 代码无需变更

**现状**：Order MCP Server 和 Product MCP Server 均通过 `FastMCP` 实现，以 stdio 传输协议与 Agent 通信。

### ADR-002: 混合检索而非纯向量检索

**决策**：RAG 检索采用 BM25 + FAISS 混合策略 + Reranker 多路召回，而非单纯依赖向量相似度。

**理由**：

- **互补性**：关键词匹配（BM25）适合精确术语查询（如订单号、策略编号）；语义向量适合模糊表述（如"退货流程"、"怎么退款"）
- **鲁棒性**：单一向量检索在领域术语或冷门 topic 上容易失效；混合策略弥补彼此的盲区
- **可量化提升**：基于 46 条测试用例，v2.0 混合检索命中率@3 从 72% 提升至 91%，MRR 从 0.65 提升至 0.88

### ADR-003: SSE 而非 WebSocket

**决策**：流式推送采用 Server-Sent Events (SSE)，而非 WebSocket。

**理由**：

- **单向推送足够**：AI 对话场景中，服务端向客户端推送 token 流即可，客户端只需发送一次请求
- **实现简单**：FastAPI 原生支持 `sse-starlette`，无需额外管理连接生命周期
- **兼容性好**：SSE 基于标准 HTTP，可穿透大多数代理和防火墙
- **自动重连**：浏览器原生 `EventSource` API 自带断线重连机制

### ADR-004: Prompt YAML 配置化

**决策**：系统提示词从 YAML 配置文件加载，而非硬编码在 Python 源码中。

**理由**：

- **版本管理**：Prompt 修改可独立进行 Git 版本控制，不污染业务逻辑代码
- **A/B 测试**：通过 `.env` 中的 `PROMPT_AB_ACTIVE` 开关，可在运行时切换不同版本的 Prompt 进行评估
- **团队协作**：产品和运营人员可直接修改 Prompt 配置文件，无需理解 Python 代码
- **多环境适配**：不同环境（dev/staging/prod）可加载不同的 Prompt 配置

**现状**：当前 Gateway 和 Manager 的 Prompt 仍以字符串形式内联在代码中，后续版本将迁移至 `prompts/` 目录下的 YAML 文件。

### ADR-005: 层级化智能体而非单一巨型 Agent

**决策**：采用三层智能体架构（Gateway -> Manager -> SubAgents），而非单个大型 Agent 处理所有请求。

**理由**：

- **职责分离**：每层聚焦特定领域，系统提示词精简有效，降低幻觉概率
- **独立迭代**：Order Agent 和 Product Agent 可独立开发、测试和部署
- **故障隔离**：某一子智能体异常不影响其他领域的功能
- **Token 经济**：子智能体仅接收相关上下文，避免全量 Context 注入造成的 token 浪费

---

## 目录结构

```text
project-root/
├── agents/                    # 智能体核心逻辑
│   ├── gateway_agent.py       # 网关智能体（入口、摘要、中间件组装）
│   ├── manager_agent.py       # 经理智能体（中枢调度、RAG 调用）
│   ├── order_agent.py         # 订单智能体（MCP 工具调用）
│   ├── product_agent.py       # 商品智能体（MCP 工具调用）
│   ├── mcp_wrapper.py         # MCP 工具包装器（User_ID 自动注入）
│   ├── model.py               # 模型配置中心（所有 LLM/Embedding 统一入口）
│   └── RAG_tool.py            # RAG 检索工具实现
├── data/                      # 业务数据库目录
│   ├── orders.db              # 订单数据库（SQLite）
│   ├── products.db            # 商品数据库（SQLite）
│   └── create_db.py           # 数据库初始化脚本
├── Mcpserver/                 # MCP 服务端实现
│   ├── order_mcp.py           # 订单数据 MCP Server（FastMCP）
│   └── product_mcp.py         # 商品数据 MCP Server（FastMCP）
├── RAG_data/                  # 知识库源文件（.md / .docx / .txt）
├── service/                   # FastAPI 服务入口
│   └── main.py                # API 路由 + 生命周期管理
├── evaluation/                # 评估测试
│   ├── evaluate_system.py     # LangSmith 评估脚本
│   ├── test_dataset.json      # 46 条测试用例
│   └── test.py                # 单元测试
├── html/                      # 前端演示界面
├── docs/                      # 项目文档
├── docker-compose.yml         # Docker 编排（6 个服务）
├── Dockerfile                 # 应用镜像构建
├── .env                       # 环境变量配置
├── requirements.txt           # 生产依赖
└── run.sh                     # 一键启动脚本
```

---

## 部署架构

Docker Compose 编排 6 个服务：

| 服务 | 容器名 | 端口 | 说明 |
|------|--------|------|------|
| `redis-stack` | cs-redis | 6379 | 对话状态持久化 + 缓存 |
| `milvus` | cs-milvus | 19530, 9091 | 向量数据库 |
| `etcd` | cs-etcd | 2379 | Milvus 元数据管理 |
| `minio` | cs-minio | 9000, 9001 | Milvus 对象存储 |
| `langfuse-server` | cs-langfuse | 3000 | LLM 可观测追踪 |
| `langfuse-db` | cs-langfuse-db | 5432 | Langfuse 元数据存储（PostgreSQL） |
| `app` | cs-app | 8000 | FastAPI 应用服务 |
