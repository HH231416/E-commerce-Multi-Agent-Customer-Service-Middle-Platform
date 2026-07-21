# 生产化能力与部署边界

本项目保留单机 Mock 数据以便本地演示，但服务边界按真实电商部署方式设计。Docker Compose 仅用于联调，不应作为多租户生产集群的唯一部署方案。

## 已落地能力

### 身份与租户边界

- 生产环境拒绝匿名访问，`service/security.py` 校验 Bearer JWT，并从令牌提取用户、租户和角色。
- URL 中的 `user_id` 只用于与令牌主体做一致性校验；会话键、MCP 工具用户上下文和缓存命名空间均来自认证声明。
- 客服工单接口要求 `support` 或 `admin` 角色，工单查询按 `tenant_id` 隔离。

### 高风险订单操作

- MCP 仅生成退款草案，不能直接改订单。
- 用户确认退款时必须携带幂等键。开发环境用 SQLite Mock 验证条件更新和重复请求；生产环境调用订单域服务，并传递服务令牌、租户、操作者和幂等键。
- 真实订单库由订单域服务拥有，客服系统不能跨域直接写库。

### RAG 与缓存

- 知识库索引通过 `python RAG_data/build_index.py` 在发布阶段构建；默认后端为 Milvus，按知识版本发布为独立 collection。FAISS 仅通过显式设置 `RAG_BACKEND=faiss` 用于本地回退。线上服务只加载已发布索引，避免首个用户请求构建索引。
- 文档元数据包含文档标识和知识版本，回答附带来源与版本。
- 缓存只处理公共稳定回答，订单类问题不缓存；缓存键包含租户、知识版本和可见范围。

### 可观测与数据基础

- FastAPI 自动注入请求标识，并暴露仅管理员可访问的 `/metrics` Prometheus 指标；OpenTelemetry 可连接企业采集器。
- Alembic 迁移提供 `tenants`、`support_tickets`、`ticket_events`、`refund_requests` 与 `audit_events` 的 PostgreSQL 模式；生产转人工、客服队列和工单状态流转实际通过异步 PostgreSQL 仓储执行。
- 生产启动会检查 PostgreSQL 连通性；Redis checkpoint 不可用时拒绝启动，避免实例间会话丢失。

## 生产部署方式

1. 在网关或身份平台完成 TLS、OIDC 登录、限流和 JWT 签发；应用只验证内部约定的令牌声明。
2. 将 PostgreSQL、Redis、对象/卷挂载的 RAG 索引、订单服务部署为受管依赖；应用实例可水平扩容。
3. 发布顺序为：审核知识文档 -> 构建并校验 RAG 索引 -> 执行 `alembic upgrade head` -> 灰度发布应用 -> 观察错误率、延迟和业务反馈。
4. 本地联调使用 `docker compose up --build`；单环境生产验证使用 `docker compose -f docker-compose.yml -f docker-compose.production.yml up --build`，该生产覆盖默认部署 Milvus。通过应用容器运行 `python RAG_data/build_index.py` 发布知识版本。生产机密必须通过平台密钥管理注入，不写入 `.env` 或镜像。

## 如实表述的边界

- 当前订单、商品、物流和优惠券数据仍是 SQLite Mock；生产路径通过订单域服务适配器体现跨服务调用边界。
- SQLite 工单仓储只在开发和测试环境启用；生产环境会自动选择 PostgreSQL 工单仓储并要求数据库已完成 Alembic 迁移。
- 当前 JWT 验证使用共享密钥演示企业网关签发后的令牌校验；接入真实 OIDC 时应替换为 JWKS 公钥验证。
