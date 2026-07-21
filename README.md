# 🛒 电商多智能体智能客服平台

# E-Commerce Multi-Agent Intelligent Customer Service Platform

基于 **LangGraph** 和 **Model Context Protocol (MCP)** 构建的电商多智能体客服中台验证项目。采用层级化多智能体架构（Gateway-Manager-SubAgents），覆盖在线咨询、知识检索、人工转接和对话洞察驱动的知识运营闭环。

本项目用于验证企业级 AI 应用的关键设计，不宣称已承载真实生产流量。当前已实现能力、后续演进边界与面试讲解主线以 [企业级设计基线](docs/ENTERPRISE_PROJECT_DESIGN.md) 为准。

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
  * 当前使用 BM25 + FAISS + Reranker 混合检索，实现政策、SOP 及常见问题的关键词和语义召回。
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
* **RAG & 向量库**: Milvus（生产默认）、BM25、BGE Reranker；FAISS 仅作为显式本地回退
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
├── data/                   # 业务数据库目录
│   ├── orders.db           # 订单数据库 (SQLite)
│   ├── products.db         # 商品数据库 (SQLite)
│   └── create_db.py        # 数据库初始化脚本
├── Mcpserver/              # MCP 服务端实现
│   ├── order_mcp.py        # 订单数据 MCP Server
│   └── product_mcp.py      # 商品数据 MCP Server
├── RAG_data/               # 知识库源文件 (.md, .docx, .txt)
├── service/                # FastAPI 服务入口
│   └── main.py
├── html/                   # 前端演示界面
├── run.sh                  # 启动脚本
└── .env                    # 环境变量配置文件
```

## 🚀 快速开始 (Getting Started)

### 1. 环境准备

确保已安装 Python 3.10+ 和 Redis 服务。

```bash
git clone https://github.com/您的用户名/您的仓库名.git
pip install -r requirements.txt
```

### 2. 配置环境变量

在项目根目录创建 `.env` 文件：

```env
# DashScope API Key（当前默认 OpenAI 兼容模型服务）
DASHSCOPE_API_KEY=sk-
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

# Embedding 模型配置（可选，有默认值）
EMBEDDING_MODEL=text-embedding-v3

# 数据库路径配置（可选，默认使用项目内 data 目录）
# ORDER_DB_PATH=/path/to/orders.db
# PRODUCT_DB_PATH=/path/to/products.db

# Redis 配置
REDIS_URL=redis://:password@localhost:6379/0
```

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

### 5. 使用 Docker 本地运行

```bash
docker compose up --build
```

服务默认运行在 `http://localhost:8000/html/index.html`。



## 关闭终端
netstat -ano | findstr :8000
taskkill /F /PID ID  

## 📄 许可证
[MIT License](LICENSE)

## 面试材料

[生产化能力与部署边界](docs/PRODUCTION_CAPABILITIES.md) 说明已落地的认证隔离、受控退款、RAG 发布、可观测和 PostgreSQL 演进，以及当前 Mock 实现的真实边界。

[项目简历与面试讲解手册](docs/INTERVIEW_GUIDE.md) 包含四点简历版、项目讲解主线、高频技术追问及指标口径。
