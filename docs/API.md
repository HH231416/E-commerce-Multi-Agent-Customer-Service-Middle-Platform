# API 接口文档

## 1. 基本约定

- Base URL：`http://localhost:8000`
- Content-Type：`application/json`；流式对话使用 `text/event-stream`
- 交互式文档：`/docs`
- 除健康检查和根路径外，接口在生产环境均应携带 `Authorization: Bearer <JWT>`。

JWT 是可信身份来源，包含租户、主体和角色。路径中的 `{user_id}` 仅用于校验其是否与令牌主体一致；它不是身份切换参数，不能用于读取其他用户的会话或订单。

## 2. 接口概览

| 方法 | 路径 | 权限/用途 |
|---|---|---|
| GET | `/` | 服务信息 |
| GET | `/api/health` | 健康检查 |
| POST | `/api/auth/session` | 用 Bearer 令牌换取 HttpOnly 会话 Cookie |
| DELETE | `/api/auth/session` | 清除会话 Cookie |
| GET | `/api/cache/stats` | `support`/`admin` 查看缓存统计 |
| GET | `/metrics` | `admin` 查看 Prometheus 指标 |
| POST | `/api/chat/{user_id}` | 认证用户的非流式对话 |
| POST | `/api/chat/{user_id}/stream` | 认证用户的 SSE 流式对话 |
| GET | `/api/chat_history/{user_id}` | 获取自己的会话历史 |
| POST | `/api/init_chat/{user_id}` | 初始化自己的会话 |
| GET | `/api/feedback/{user_id}` | 提交自己的 1–5 分反馈 |
| GET | `/api/transfer/tickets` | `support`/`admin` 查询本租户工单 |
| GET | `/api/transfer/tickets/{ticket_id}` | `support`/`admin` 查询本租户工单 |
| POST | `/api/transfer/tickets/{ticket_id}/assign` | `support`/`admin` 指派自己处理工单 |
| POST | `/api/transfer/tickets/{ticket_id}/resolve` | `support`/`admin` 解决工单 |
| GET | `/api/transfer/queue` | `support`/`admin` 查看本租户队列 |
| POST | `/api/orders/{order_no}/refund/confirm` | 认证用户确认退款 |

### 鉴权来源与浏览器会话

服务端按以下顺序解析身份：非生产环境的调试身份头 → `Authorization: Bearer` → HttpOnly 会话 Cookie。

浏览器端推荐流程：登录时调用 `POST /api/auth/session` 携带 Bearer 令牌，服务端校验签发方与受众后下发 `cs_session` Cookie（`HttpOnly`、`SameSite=Lax`、生产环境带 `Secure`），前端随即丢弃令牌。此后由浏览器自动携带 Cookie。

使用 Cookie 鉴权的写请求（非 GET/HEAD/OPTIONS）会校验 `Origin` 与 `Host` 是否一致，不一致返回 403；非浏览器客户端通常不带 `Origin`，不受此限制。会话 Cookie 的 `Max-Age` 取自令牌 `exp`。

## 3. 对话接口

### POST `/api/chat/{user_id}`

非流式对话。请求在 Gateway 完成身份、租户和会话校验后，交由 Manager 生成并校验任务计划；领域查询通过 MCP 工具执行，政策问答走当前知识版本的混合检索。

请求：

```http
Authorization: Bearer <JWT>
Idempotency-Key: optional-request-key
Content-Type: application/json

{"message":"订单 A1001 到哪里了？如果今天不到能退款吗？"}
```

`Idempotency-Key` 适用于客户端重试，同一用户和租户下应保持唯一。请求体中的 `message` 为必填，长度受服务端限制。

响应至少包含回答和角色；服务端可返回延迟等非敏感统计信息。业务事实以 MCP 返回为准，政策回答应附带来源与知识版本；关键事实不足或高风险操作未确认时，系统会追问、拒答或转人工。

```json
{
  "response": "已查询到订单的最新状态……",
  "role": "assistant",
  "latency_ms": 1234
}
```

### POST `/api/chat/{user_id}/stream`

SSE 流式对话。鉴权与 `{user_id}` 一致性校验规则同非流式接口。支持的核心事件：

| 事件 | 数据 | 说明 |
|---|---|---|
| `thinking` | `step` | 仅展示安全的处理进度，不泄露内部 Prompt 或敏感参数 |
| `token` | `content` | 回复增量文本 |
| `transfer` | `reason`、`ticket_id`、`message` | 系统已创建或复用人工工单 |
| `done` | `total_tokens`、`cost`、`latency_ms`、可选 `cached` | 请求完成统计 |
| `error` | `message` | 可展示的失败原因 |

工具调用参数、用户身份、订单号和其他敏感信息不应直接作为 SSE 事件输出。若命中隔离后的稳定 FAQ 缓存，响应可带 `cached: true`；订单、物流、价格、库存和退款状态不使用普通语义缓存。

## 4. 会话与反馈

### GET `/api/chat_history/{user_id}`

返回当前认证主体在当前租户下的会话历史。系统消息和工具消息会被过滤；未初始化或没有会话时返回空数组。

```json
{"history":[{"role":"user","content":"我的订单怎么还没到？"},{"role":"assistant","content":"请提供订单号。"}]}
```

### POST `/api/init_chat/{user_id}`

幂等地初始化当前用户会话。已有历史时直接返回，不应重复写入示例消息。

### GET `/api/feedback/{user_id}?score=1`

记录当前用户的 1–5 分反馈，并关联当前 Prompt 版本以便评估。评分不在范围内返回 `422`。

## 5. 人工工单接口

当用户显式要求人工、出现高风险问题或负面情绪达到阈值时，Gateway 创建或复用工单。工单查询和处理都按 `tenant_id` 隔离，且要求 `support` 或 `admin` 角色。

- `GET /api/transfer/tickets`：可按状态查询本租户工单及待处理数量。
- `GET /api/transfer/tickets/{ticket_id}`：获取单个本租户工单。
- `POST /api/transfer/tickets/{ticket_id}/assign`：请求体 `{"agent_id":"当前认证客服"}`，只能把工单指派给自己。
- `POST /api/transfer/tickets/{ticket_id}/resolve`：请求体包含当前客服 `agent_id` 与 `resolution_note`。
- `GET /api/transfer/queue`：获取本租户待处理队列。

## 6. 退款确认接口

### POST `/api/orders/{order_no}/refund/confirm`

此接口只能在系统已生成退款草案、用户明确确认后调用。接口不是“模型调用退款”的捷径：服务端仍会校验认证主体、租户、订单归属、订单状态、确认令牌/草案和幂等键，并写入审计事件。

请求示例：

```http
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "idempotency_key":"refund_20260110_abc123",
  "reason":"商品未按约定时间送达"
}
```

`idempotency_key` 长度 16–128，只允许字母、数字、下划线和连字符。业务冲突或重复确认返回 `409`；认证或归属校验失败返回 `401`/`403`；未找到资源返回 `404`。

## 7. 错误与安全约定

| 状态码 | 含义 |
|---:|---|
| 400 | 请求格式、消息内容或业务前置条件错误 |
| 401 | 缺少、过期或无效令牌 |
| 403 | 角色、租户、主体或资源归属不匹配 |
| 404 | 当前租户下不存在资源 |
| 409 | 幂等键冲突、重复确认或状态冲突 |
| 422 | 字段校验失败，例如评分范围或幂等键格式 |
| 500 | 未预期服务异常；不向客户端暴露 Traceback |
| 503 | Agent 或关键依赖尚未就绪 |

生产环境应在 API Gateway 完成 TLS、限流、JWT 签发和基础防护；应用层继续做主体一致性、租户隔离、工具授权和审计，不以客户端传入的身份或工具参数作为授权依据。
