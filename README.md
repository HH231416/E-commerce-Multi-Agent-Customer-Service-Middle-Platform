# 🛒 电商多智能体客服中台

# E-Commerce Multi-Agent Intelligent Customer Service Platform

基于 **LangGraph** 和 **Model Context Protocol (MCP)** 构建的电商多智能体客服中台验证项目。采用层级化多智能体架构（Gateway-Manager-SubAgents），覆盖在线咨询、知识检索、人工转接和对话洞察驱动的知识运营闭环。

本项目用于验证企业级 AI 应用的关键设计，不宣称已承载真实生产流量。当前已实现能力与后续演进边界以 [企业级设计基线](docs/ENTERPRISE_PROJECT_DESIGN.md) 为准。

---

## 🌟 核心特性 (Key Features)

* **🧠 层级化智能体架构**:
  * **Gateway Agent**: 流量入口，负责会话管理、历史消息摘要及路由。
  * **Manager Agent**: 业务总控，负责意图识别、任务拆解及子智能体调度。
  * **Domain Sub-Agents**: 专职子智能体（Order Agent, Product Agent），专注于特定领域业务。
* **🔌 MCP (Model Context Protocol) 集成**:
  * 采用标准化 MCP 协议连接 SQLite 数据库，实现工具调用的解耦与标准化。
  * 支持透明化的上下文注入（如自动注入 `user_id`）。
* **📚 RAG 知识检索增强**:
  * 支持多格式文档加载（.docx, .md, .txt）。
  * 生产后端使用 Milvus 统一混合检索，结合稠密向量、BM25 稀疏向量和 Reranker 实现政策、SOP 及常见问题召回。
* **⚡ 全异步高性能架构**:
  * 基于 FastAPI + Asyncio 实现全链路异步处理。
  * 集成 AsyncRedisSaver 实现分布式状态持久化。
* **🛠️ 强大的中间件机制**:
  * **Summarization**: 自动长对话摘要，优化 Context 窗口。
  * **Self-Healing**: 具备工具调用异常检测与自动修复机制。

## 🏗️ 技术栈 (Tech Stack)

* **核心框架**: [LangChain](https://www.langchain.com/), [LangGraph](https://langchain-ai.github.io/langgraph/), DeepAgents (Custom Wrapper)
* **工具协议**: [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
* **API 服务**: FastAPI, Uvicorn
* **持久化 & 缓存**: Redis (AsyncCheckpoint), SQLite (Mock 业务数据)
* **RAG & 向量库**: Milvus、BM25、BGE Reranker
* **大语言模型**:
  * 通过环境变量配置 OpenAI 兼容模型服务，当前默认配置为 DashScope/Qwen 系列。

## 📂 项目结构 (Directory Structure)

```text
Multi_Agents/
├── agents/                 # 智能体核心逻辑
│   ├── gateway_agent.py    # 网关智能体 (入口、摘要、中间件组装)
│   ├── manager_agent.py    # 经理智能体 (中枢调度、RAG 调用)
│   ├── order_agent.py      # 订单智能体 (MCP工具调用)
│   ├── product_agent.py    # 商品智能体 (MCP工具调用)
│   ├── logistics_agent.py  # 物流智能体 (MCP工具调用)
│   ├── coupon_agent.py     # 优惠券智能体 (MCP工具调用)
│   ├── human_handoff.py    # 人工转接工单流程
│   ├── cache_layer.py      # 语义缓存
│   ├── mcp_wrapper.py      # MCP 工具包装器 (User_ID 注入)
│   ├── model.py            # 模型配置 (LLM、Embedding)
│   └── RAG_tool.py         # RAG 检索工具实现
├── data/                   # 匿名演示数据与数据库初始化脚本
│   ├── order_table.xlsx    # 演示订单数据
│   └── create_db.py        # SQLite Mock 数据库初始化脚本
├── Mcpserver/              # MCP 服务端实现
│   ├── order_mcp.py        # 订单数据 MCP Server
│   └── product_mcp.py      # 商品数据 MCP Server
├── RAG_data/               # 匿名知识库样例与索引构建脚本
├── evaluation/             # 离线评测集与评测脚本
├── migrations/             # PostgreSQL/Alembic 数据迁移
├── prompts/                # Prompt 版本与实验配置
├── service/                # FastAPI 服务、鉴权与业务工作流
├── tests/                  # 核心流程自动化测试
├── html/                   # 前端演示界面
├── run.sh                  # 启动脚本
└── .env.example            # 无真实凭据的配置模板
```

## 🚀 快速开始 (Getting Started)

### 1. 环境准备

确保已安装 Python 3.10+ 和 Redis 服务。

```bash
git clone https://github.com/您的用户名/您的仓库名.git
pip install -r requirements.txt
```

### 2. 配置环境变量

复制配置模板，并在本地 `.env` 中填写真实值：

```bash
cp .env.example .env
```

`.env` 已加入忽略规则；请勿提交真实密钥、令牌或生产连接信息。

### 3. 初始化数据 (首次运行必须)

初始化业务数据库：

```bash
python data/create_db.py
```

如需重建 RAG 向量索引（可选）：

```bash
python RAG_data/create_data.py
```

### 4. 启动服务

```bash
# 在项目根目录运行
python service/main.py
```

服务默认访问地址为 `http://localhost:8000/html/index.html`。

### 5. 使用 Docker 部署

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up --build
```

服务默认运行在 `http://localhost:8000/html/index.html`。


## 📚 设计文档

- [系统架构](docs/ARCHITECTURE.md)
- [企业级设计基线](docs/ENTERPRISE_PROJECT_DESIGN.md)
- [RAG 检索设计](docs/RAG_DESIGN.md)
- [生产化能力与部署边界](docs/PRODUCTION_CAPABILITIES.md)
- [API 说明](docs/API.md)
