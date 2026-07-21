# API 接口文档

## 基本信息

- **Base URL**：`http://localhost:8000`
- **Content-Type**：`application/json`
- **编码**：`UTF-8`
- **交互式文档**：启动服务后访问 `http://localhost:8000/docs`（Swagger UI）
- **前端页面**：启动服务后访问 `http://localhost:8000/html/index.html`

---

## 接口概览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`  | `/` | 根路径，返回 API 基本信息 |
| `GET`  | `/api/health` | 健康检查 |
| `POST` | `/api/chat/{user_id}` | 传统一次性对话（非流式） |
| `POST` | `/api/chat/{user_id}/stream` | SSE 流式对话（推荐） |
| `GET`  | `/api/chat_history/{user_id}` | 获取用户对话历史 |
| `POST` | `/api/init_chat/{user_id}` | 初始化新对话 |
| `GET`  | `/api/feedback/{user_id}` | 用户反馈收集 |

---

## 接口详情

### GET /

根路径，用于确认服务运行状态。

**响应示例**：

```json
{
  "message": "电商多智能体智能客服平台 API",
  "docs": "/docs"
}
```

---

### GET /api/health

健康检查接口，供负载均衡器或监控系统使用。

**响应示例**：

```json
{
  "status": "healthy"
}
```

**状态码**：

| 状态码 | 说明 |
|--------|------|
| 200 | 服务正常 |

---

### POST /api/chat/{user_id} — 传统一次性返回

向 AI 客服发送消息并获取完整回复。此接口等待 Agent 完成全部处理（包括子智能体调用、MCP 工具执行、RAG 检索）后一次性返回结果。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 用户唯一标识，同时作为 `thread_id` 用于 Redis 状态持久化 |

**请求体**：

```json
{
  "message": "查询订单A1001的状态"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户输入的消息内容 |

**成功响应**：

```json
{
  "response": "您的订单 A1001 当前状态为：待发货。包含商品：路由器 x1、网线 x2，支付金额：598.00 元。",
  "role": "assistant"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `response` | string | AI 客服的回复内容 |
| `role` | string | 固定为 `"assistant"` |

**错误响应**：

| 状态码 | detail | 说明 |
|--------|--------|------|
| 400 | `"消息内容不能为空"` | 请求体中缺少 `message` 字段或为空 |
| 500 | `"对话失败: ..."` | Agent 处理异常（含 traceback） |
| 503 | `"Agent is not initialized yet."` | 服务启动未完成，Agent 尚未就绪 |

**注意事项**：

- 对话历史由 `AsyncRedisSaver` 自动管理，同一 `user_id` 的请求会延续之前的上下文
- 若上次请求因崩溃留下未完成的工具调用，系统会自动注入修复消息恢复状态闭环
- 此接口为阻塞式调用，处理时间取决于 LLM 响应速度与子智能体调用次数（通常 3-15 秒）

---

### POST /api/chat/{user_id}/stream — SSE 流式返回（推荐）

与 AI 客服进行流式对话，通过 Server-Sent Events (SSE) 实时推送处理进度。相比传统接口，用户可感知系统正在处理的各个阶段。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 用户唯一标识，同时作为 `thread_id` 用于 Redis 状态持久化 |

**请求体**：

```json
{
  "message": "帮我查一下夏科键盘的价格"
}
```

**响应格式**：`text/event-stream` (SSE)

**SSE 事件类型**：

#### `event: thinking`

系统正在执行的思考步骤，用于向用户展示处理进度。

```
event: thinking
data: {"step": "正在理解您的问题..."}
```

```
event: thinking
data: {"step": "识别为商品查询，正在为您搜索..."}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `step` | string | 当前处理步骤的描述文字 |

#### `event: tool_start`

工具调用开始，用于展示系统正在调用哪个后端服务。

```
event: tool_start
data: {"tool": "get_product_info", "args": {"product_description": "夏科键盘"}}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool` | string | 被调用的工具名称 |
| `args` | object | 传递给工具的参数（已隐藏敏感字段如 user_id） |

#### `event: tool_end`

工具调用完成，附带结果摘要。

```
event: tool_end
data: {"tool": "get_product_info", "summary": "已找到商品：夏科有线键鼠套装，价格50.00元，库存充足"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool` | string | 工具名称 |
| `summary` | string | 工具执行结果的简短摘要 |

#### `event: token`

流式输出的文本片段，用于在前端逐字展示 AI 回复。

```
event: token
data: {"content": "夏科"}
```

```
event: token
data: {"content": "有线键鼠"}
```

```
event: token
data: {"content": "套装"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | 增量文本内容，前端应累积展示 |

#### `event: transfer`

当系统判断需要转人工客服时触发。

```
event: transfer
data: {"reason": "用户情绪不稳定且问题超出自动处理范围", "ticket_id": "TKT-20240601-001", "message": "正在为您转接人工客服，请稍候..."}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `reason` | string | 转人工的原因说明 |
| `ticket_id` | string | 生成的人工工单编号 |
| `message` | string | 展示给用户的提示信息 |

#### `event: done`

对话完成，包含本次对话的统计信息。

```
event: done
data: {"total_tokens": 1247, "cost": 0.0032, "latency_ms": 4230}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `total_tokens` | integer | 本次对话消耗的总 token 数 |
| `cost` | float | 本次对话的费用（美元） |
| `latency_ms` | integer | 端到端延迟（毫秒） |

#### `event: error`

处理过程中发生错误。

```
event: error
data: {"message": "订单查询超时，请稍后重试"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | string | 错误描述信息 |

#### SSE 流完整示例

```
event: thinking
data: {"step": "正在理解您的问题..."}

event: thinking
data: {"step": "已识别为商品查询请求"}

event: tool_start
data: {"tool": "get_product_info", "args": {"product_description": "夏科键盘"}}

event: tool_end
data: {"tool": "get_product_info", "summary": "已找到商品：夏科有线键鼠套装"}

event: token
data: {"content": "夏科"}

event: token
data: {"content": "有线键鼠套装"}

event: token
data: {"content": "目前售价"}

event: token
data: {"content": "50.00"}

event: token
data: {"content": "元"}

event: done
data: {"total_tokens": 856, "cost": 0.0012, "latency_ms": 3120}
```

**客户端实现参考（JavaScript）**：

```javascript
const eventSource = new EventSource('/api/chat/user123/stream');

eventSource.addEventListener('thinking', (e) => {
  const data = JSON.parse(e.data);
  showProgress(data.step);
});

eventSource.addEventListener('tool_start', (e) => {
  const data = JSON.parse(e.data);
  showToolCall(data.tool);
});

eventSource.addEventListener('tool_end', (e) => {
  const data = JSON.parse(e.data);
  hideToolCall(data.tool);
});

eventSource.addEventListener('token', (e) => {
  const data = JSON.parse(e.data);
  appendText(data.content);
});

eventSource.addEventListener('transfer', (e) => {
  const data = JSON.parse(e.data);
  showTransfer(data.message, data.ticket_id);
  eventSource.close();
});

eventSource.addEventListener('done', (e) => {
  const data = JSON.parse(e.data);
  showStats(data.total_tokens, data.cost, data.latency_ms);
  eventSource.close();
});

eventSource.addEventListener('error', (e) => {
  const data = JSON.parse(e.data);
  showError(data.message);
  eventSource.close();
});
```

---

### GET /api/chat_history/{user_id}

获取指定用户的对话历史记录。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 用户唯一标识 |

**成功响应**：

```json
{
  "history": [
    {
      "role": "user",
      "content": "我的订单怎么还没到？"
    },
    {
      "role": "assistant",
      "content": "请告诉我你要查询的订单号？"
    },
    {
      "role": "user",
      "content": "A1001"
    },
    {
      "role": "assistant",
      "content": "您的订单 A1001 当前状态为：待发货。包含商品：路由器 x1、网线 x2。"
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `history` | array | 消息列表，按时间顺序排列 |
| `history[].role` | string | 消息发送者：`"user"` 或 `"assistant"` |
| `history[].content` | string | 消息内容 |

**注意事项**：

- 系统消息（SystemMessage）和工具消息（ToolMessage）已自动过滤
- 重复消息（相同 ID）已自动去重
- 未知类型的消息会被跳过并记录警告日志
- 若无历史记录，返回 `{"history": []}`

**错误响应**：

| 状态码 | 说明 |
|--------|------|
| 500 | 读取 Redis 状态失败 |
| 503 | Agent 尚未初始化 |

---

### POST /api/init_chat/{user_id}

初始化用户对话。若用户已有对话历史则返回现有历史；否则创建一组示例对话作为初始上下文。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 用户唯一标识 |

**成功响应（新对话）**：

```json
{
  "history": [
    {
      "role": "user",
      "content": "我的订单怎么还没到？"
    },
    {
      "role": "assistant",
      "content": "请告诉我你要查询的订单号？"
    }
  ]
}
```

**成功响应（已有历史）**：

```json
{
  "history": [
    {
      "role": "user",
      "content": "查询订单A1001"
    },
    {
      "role": "assistant",
      "content": "您的订单 A1001 当前状态为：待发货..."
    }
  ]
}
```

**注意事项**：

- 此接口为幂等操作：多次调用不会重复初始化
- 初始化消息会通过 `ainvoke` 写入 `AsyncRedisSaver`，确保后续对话可延续上下文

---

### GET /api/feedback/{user_id}

收集用户对本次服务的满意度反馈。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 用户唯一标识 |

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `score` | integer | 是 | 满意度评分：`1`（满意）或 `0`（不满意） |

**请求示例**：

```
GET /api/feedback/user123?score=1
```

**成功响应**：

```json
{
  "status": "received",
  "user_id": "user123",
  "score": 1,
  "message": "感谢您的反馈！"
}
```

**注意事项**：

- 反馈数据会记录到 Langfuse Trace，便于后续分析和模型优化
- 可通过 Langfuse 控制台统计满意率、分析不满意案例

---

## 全局错误码

| 状态码 | 说明 | 处理建议 |
|--------|------|---------|
| 400 | 请求参数错误 | 检查请求体 JSON 格式和必填字段 |
| 500 | 服务器内部错误 | 查看服务日志，通常包含详细 traceback |
| 503 | 服务未就绪 | 等待服务启动完成（Agent 初始化需要连接 Redis/Milvus） |

---

## 接口调用时序

```
   客户端                                      服务端

     |---- POST /api/init_chat/user123 --------->|  (初始化对话)
     |<--- {history: [...]} --------------------|
     |                                          |
     |---- POST /api/chat/user123 -------------->|  (发送消息)
     |       {"message": "查订单A1001"}         |
     |                                          |---- Gateway Agent
     |                                          |       |-- SummarizationMiddleware
     |                                          |       |-- SubAgentMiddleware -> Manager
     |                                          |                         |-- Order Agent
     |                                          |                         |     |-- MCP get_order
     |                                          |                         |     |-- SQLite 查询
     |                                          |                         |-- 结果整合
     |<--- {"response": "...", "role": "..."] --|
     |                                          |
     |---- GET /api/chat_history/user123 ------->|  (获取历史)
     |<--- {history: [...]} --------------------|
     |                                          |
     |---- GET /api/feedback/user123?score=1 --->|  (提交反馈)
     |<--- {"status": "received"} --------------|
```
