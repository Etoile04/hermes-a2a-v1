# hermes-a2a-v1 Next Development Plan

**Date:** 2026-05-25
**Status:** Draft
**Author:** Hermes Agent + User (Wenjie Li)

---

## Overview

hermes-a2a-v1 当前已完成 Phase 1（本地开发 + 41 测试）和 Phase 2（跨机器部署验证）。本文档规划下一步开发，包括：
1. **生产级加固** — 让 gateway 真正可靠运行
2. **OpenClaw A2A 互操作** — 与 OpenClaw 的 a2a-gateway 插件双向通讯
3. **架构重构** — 消除技术债，为扩展做准备

---

## 关键发现：协议版本差异

| 组件 | A2A SDK | 协议版本 | JSON-RPC 方法名 |
|------|---------|----------|-----------------|
| **hermes-a2a-v1** (本项目) | Python `a2a-sdk` 1.0.3 | **v1.0** | `SendMessage`, `GetTask`, `ListTasks`... |
| **OpenClaw a2a-gateway** | Node.js `@a2a-js/sdk` ^0.3.13 | **v0.3.0** | `message/send`, `tasks/get`... |

**好消息：** Python a2a-sdk 1.0.3 内置了 `a2a.compat.v0_3` 兼容层：
- `enable_v0_3_compat=True` 参数让同一 endpoint 同时处理 v1.0 和 v0.3 请求
- v0.3 方法 `message/send` 自动映射到 `SendMessage` handler
- 只需一行代码即可启用

---

## Phase 3: Production Hardening (3-4 天)

### Task 3.1: 启用 v0.3 兼容层 [0.5 day]

**Goal:** 让 hermes-a2a-v1 同时接受 v1.0 和 v0.3 客户端请求

**Changes:**
```python
# server.py - create_jsonrpc_routes 调用处
rpc_routes = create_jsonrpc_routes(
    handler,
    rpc_url="/a2a/jsonrpc",
    enable_v0_3_compat=True,  # ← 新增这一行
)
```

**Tests:**
- 用 v0.3 方法名 `message/send` 发送请求，验证正确处理
- 用 v1.0 方法名 `SendMessage` 发送请求，验证不回归
- 混合 v0.3/v1.0 请求到同一 endpoint

### Task 3.2: 错误处理与重试 [1 day]

**Goal:** HermesClient 具备生产级可靠性

**Changes in `hermes_client.py`:**
1. 添加指数退避重试（`tenacity` 或手动实现）
   - 连接错误：重试 3 次，间隔 1s/2s/4s
   - 超时错误：重试 2 次
   - 429/503：重试并按 Retry-After 等待
2. 添加超时分层：
   - 连接超时 10s
   - 读取超时 60s（可配置）
   - 总请求超时 300s
3. 结构化错误响应：
   - 网络错误 → Task FAILED + 有意义的错误消息
   - 401 → Task FAILED + "authentication failed"
   - 5xx → 重试 → 仍失败则 Task FAILED

**Tests:**
- mock httpx 连接超时 → 验证重试 + 最终 FAILED
- mock 429 → 验证 Retry-After 等待
- mock 3 次成功/失败交替 → 验证重试成功路径

### Task 3.3: Session 持久化 [1 day]

**Goal:** contextId→sessionId 映射不因重启丢失

**Changes:**
1. 新建 `session_store.py` — SQLite session 持久化
   ```python
   class SessionStore:
       async def save(self, context_id: str, session_id: str) -> None
       async def get(self, context_id: str) -> str | None
       async def delete(self, context_id: str) -> None
       async def cleanup(self, max_age_hours: int = 24) -> int
   ```
2. Schema: `CREATE TABLE sessions (context_id TEXT PK, session_id TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)`
3. 修改 `a2a_handler.py` 的 `_sessions` dict → 使用 `SessionStore`
4. 启动时从 SQLite 恢复已有 sessions
5. 添加 TTL 清理（默认 24h）

**Tests:**
- save → get 往返测试
- restart 恢复测试（关闭/重新打开 SQLite）
- cleanup 过期 session 测试
- 并发读写测试

### Task 3.4: 结构化日志 + 健康检查 [1 day]

**Goal:** 生产环境可观测

**Changes:**
1. 所有模块添加 `structlog` 风格日志（用标准 logging 即可）：
   - 请求日志：method, task_id, duration_ms, status
   - 错误日志：error_type, error_msg, context
   - 审计日志：a2a_audit（与 OpenClaw 的 a2a-audit.jsonl 格式对齐）
2. `/health` 端点增强：
   ```json
   {
     "status": "ok",
     "hermes_api": {"reachable": true, "latency_ms": 23},
     "task_store": {"type": "sqlite", "tasks_count": 42, "db_size_bytes": 16384},
     "sessions": {"active": 3},
     "uptime_seconds": 86400
   }
   ```
3. 添加 `/metrics` 端点（简单 JSON 格式，后续可转 Prometheus）：
   - requests_total, errors_total, active_tasks, avg_latency_ms

**Tests:**
- health 端点返回正确结构
- Hermes 不可达时 health 反映 degraded
- 日志输出格式验证

### Task 3.5: 代码清理 + 测试补充 [1 day]

**Goal:** 消除技术债

**Changes:**
1. **删除 `handler.py`** — 仅保留 `a2a_handler.py`（protobuf-based），消除重复
2. **统一 AgentCard 构建** — 删除 `agent_card.py` 中的 dict-based builder，统一到 `server.py` 中的 protobuf builder
3. **添加 `conftest.py`** — 共享 fixtures（TestClient, mock HermesClient, TaskStore）
4. **补充缺失测试**：
   - Auth middleware 测试（token 正确/错误/缺失）
   - HermesClient 流式测试
   - 错误路径测试（超时、HTTP 错误、畸形输入）
   - TaskStore 并发测试
5. **添加 `__main__.py`** — 支持 `python -m hermes_a2a`
6. **添加 README.md**

---

## Phase 4: OpenClaw A2A 互操作 (3-4 天)

### 核心架构

```
┌───────────────────────────────────────────────────────────────┐
│  Machine A (macOS)                                           │
│                                                              │
│  ┌─────────────────────────┐   ┌──────────────────────────┐  │
│  │  OpenClaw (Node.js)     │   │  Hermes Gateway          │  │
│  │  a2a-gateway plugin     │   │  ├─ Feishu               │  │
│  │  ├─ @a2a-js/sdk ^0.3.13 │   │  ├─ Discord              │  │
│  │  ├─ JSON-RPC v0.3       │──▶│  └─ API Server :8642     │  │
│  │  ├─ REST v0.3           │   │         ▲                │  │
│  │  └─ gRPC v0.3           │   └─────────│────────────────┘  │
│  └──────────┬──────────────┘             │                    │
│             │ v0.3 JSON-RPC              │                    │
│             ▼                            │                    │
│  ┌─────────────────────────┐             │                    │
│  │  hermes-a2a-v1 :18800   │─────────────┘                    │
│  │  ├─ v1.0 + v0.3 compat  │  HTTP (Bearer)                   │
│  │  ├─ REST transport       │                                  │
│  │  └─ → Hermes API        │                                  │
│  └─────────────────────────┘                                  │
└───────────────────────────────────────────────────────────────┘
         │                              │
    A2A v0.3 JSON-RPC            A2A v0.3/v1.0
         │                              │
         ▼                              ▼
┌──────────────────┐        ┌──────────────────┐
│ OpenClaw (B)     │        │ hermes-a2a-v1 (B)│
│ ThinkStation     │        │ ThinkStation     │
└──────────────────┘        └──────────────────┘
```

### Task 4.1: REST Transport 暴露 [0.5 day]

**Goal:** OpenClaw 可能优先尝试 REST transport

**Changes in `server.py`:**
```python
from a2a.server.routes import create_rest_routes

rest_routes = create_rest_routes(
    handler,
    prefix="/a2a/rest",
)
app.routes.extend(rest_routes)
```

**Tests:**
- REST endpoint `POST /a2a/rest/message/send` 工作正常
- REST endpoint `GET /a2a/rest/tasks/{id}` 工作正常
- REST 和 JSON-RPC 返回一致的结果

### Task 4.2: AgentCard 丰富 [0.5 day]

**Goal:** AgentCard 包含 OpenClaw 期望的所有字段

**Changes:**
添加以下字段到 AgentCard：
- `documentationUrl` → 指向项目 README
- `provider` → `{organization: "Hermes", url: "..."}`
- `securitySchemes` → `{bearer: {type: "http", scheme: "bearer"}}`
- `capabilities.streaming: true`
- `capabilities.pushNotifications: false`
- 正确的 `protocolVersion: "1.0"`

**Tests:**
- AgentCard JSON schema 验证
- `/.well-known/agent-card.json` 返回完整字段
- securitySchemes 结构正确

### Task 4.3: Task State Machine [1 day]

**Goal:** 验证状态转换合法性

**State Machine:**
```
SUBMITTED → WORKING → COMPLETED
                   → FAILED
                   → CANCELED (via CancelTask)
WORKING → COMPLETED
        → FAILED
        → INPUT_REQUIRED
        → CANCELED
COMPLETED → (terminal)
FAILED → (terminal)
CANCELED → (terminal)
INPUT_REQUIRED → WORKING (after user input)
```

**Changes in `a2a_handler.py`:**
1. 添加状态转换验证函数
2. `on_cancel_task` 检查当前状态是否允许取消
3. 非法转换返回 A2A 错误

**Tests:**
- 取消 COMPLETED task → 错误
- 取消 WORKING task → 成功
- 状态转换表全覆盖

### Task 4.4: Multi-part Message 支持 [1 day]

**Goal:** 正确处理 A2A FilePart 和 DataPart

**Changes:**
1. 消息解析提取所有 Part 类型（不仅是 TextPart）
2. FilePart（URI）→ 下载并附加到 Hermes 请求
3. DataPart（JSON）→ 序列化为文本描述
4. 响应中的 File URL → 自动转为 FilePart

**Tests:**
- 发送 TextPart + FilePart → 正确处理
- 发送 DataPart → 正确序列化
- 响应含文件 URL → 自动转为 FilePart

### Task 4.5: 与 OpenClaw 本地集成测试 [1.5 days]

**Goal:** 验证 OpenClaw ↔ hermes-a2a-v1 双向通讯

**步骤:**

**Part A: OpenClaw → hermes-a2a-v1（OpenClaw 作为 client）**
1. 更新 `~/.openclaw/openclaw.json` 中 a2a-gateway plugin 配置：
   ```json
   {
     "peers": [
       {
         "name": "hermes-a2a-v1",
         "agentCardUrl": "http://100.65.135.2:18800/.well-known/agent-card.json",
         "auth": { "type": "bearer", "token": "..." }
       }
     ]
   }
   ```
2. 启动 hermes-a2a-v1 gateway（v0.3 compat enabled）
3. 通过 OpenClaw client 发送 A2A message → 验证到达 Hermes → 返回响应
4. 测试不同 transport：JSON-RPC、REST

**Part B: hermes-a2a-v1 → OpenClaw（我们作为 client）**
1. 在 hermes-a2a-v1 中实现 A2A Client 功能：
   ```python
   # 新文件: src/hermes_a2a/a2a_client.py
   class A2AClient:
       """A2A client for connecting to remote A2A agents."""
       async def discover(self, agent_card_url: str) -> AgentCard
       async def send_message(self, agent_url: str, message: str, ...) -> Task
       async def get_task(self, agent_url: str, task_id: str) -> Task
       async def stream_message(self, agent_url: str, message: str, ...) -> AsyncGenerator
   ```
2. 利用 a2a-sdk 的 client 模块（如果可用），或自行实现 JSON-RPC client
3. 添加到 config：
   ```yaml
   peers:
     - name: openclaw-coding
       agent_card_url: "http://localhost:18801/.well-known/agent-card.json"
       auth: { type: "bearer", token: "..." }
   ```
4. 添加 peer discovery + routing API：
   - `GET /a2a/peers` — 列出已知 peers
   - `POST /a2a/relay` — 通过本 gateway 转发消息到指定 peer

**Part C: 端到端验收**
1. OpenClaw agent (writer) → hermes-a2a-v1 → Hermes → 回复
2. hermes-a2a-v1 → OpenClaw a2a-gateway → OpenClaw agent → 回复
3. 多轮对话（contextId 保持）
4. TaskStore 记录完整

**Tests:**
- Integration test：用 real HTTP 调用 OpenClaw a2a-gateway
- Error path：peer 不可达时的优雅降级
- Multi-turn：两轮对话共享 context

---

## Phase 5: 高级功能 (2+ 周，可选)

### Task 5.1: A2A Client SDK 封装
- 封装 a2a-sdk Python client 模块
- 支持自动 peer discovery
- 连接池管理

### Task 5.2: Push Notifications
- 实现 `CreateTaskPushNotificationConfig`
- Webhook 发送 + 重试
- 配置持久化

### Task 5.3: Docker + CI/CD
- Dockerfile + docker-compose.yaml
- GitHub Actions CI（lint + test + build）
- 自动发布到 GHCR

### Task 5.4: Redis TaskStore
- 实现 `DatabaseTaskStore`（a2a-sdk 内置的）
- 替换 SQLite 用于生产
- Session 也迁移到 Redis

### Task 5.5: 流式响应增强
- 实时 SSE streaming（Hermes → A2A SSE event stream）
- TaskArtifactUpdateEvent（中间产物）
- 心跳保活

### Task 5.6: 安全加固
- TLS termination
- OAuth 2.0 / mTLS
- Rate limiting
- CORS 配置化

### Task 5.7: Admin API
- Peer 管理（添加/删除/健康检查）
- Task 管理（查看/清理/重试）
- Metrics dashboard

---

## 实施优先级

| 优先级 | Phase | Task | 预估 | 依赖 |
|--------|-------|------|------|------|
| 🔴 P0 | 3 | 3.1 v0.3 兼容 | 0.5d | 无 |
| 🔴 P0 | 3 | 3.2 错误处理 | 1d | 无 |
| 🔴 P0 | 3 | 3.3 Session 持久化 | 1d | 无 |
| 🟡 P1 | 3 | 3.4 日志+健康检查 | 1d | 3.2 |
| 🟡 P1 | 3 | 3.5 代码清理 | 1d | 3.1, 3.2 |
| 🔴 P0 | 4 | 4.1 REST transport | 0.5d | 3.1 |
| 🔴 P0 | 4 | 4.2 AgentCard 丰富 | 0.5d | 3.5 |
| 🟡 P1 | 4 | 4.3 Task State Machine | 1d | 3.5 |
| 🟡 P1 | 4 | 4.4 Multi-part Message | 1d | 3.5 |
| 🔴 P0 | 4 | 4.5 OpenClaw 集成测试 | 1.5d | 4.1-4.4 |
| 🟢 P2 | 5 | 按需选取 | 2+ w | Phase 4 完成 |

---

## 验收标准

### Phase 3 完成
- [ ] 41+ 测试全部通过（含新增测试）
- [ ] v0.3 兼容验证（用 curl 发送 `message/send` 成功）
- [ ] HermesClient 重试 3 次仍失败 → Task 状态为 FAILED
- [ ] 重启 gateway → 多轮对话 session 仍在
- [ ] `/health` 返回 Hermes API 连通性 + DB 状态

### Phase 4 完成
- [ ] OpenClaw a2a-gateway 能发现我们的 AgentCard
- [ ] OpenClaw 发送 `message/send`（v0.3）→ 我们正确处理
- [ ] 我们能向 OpenClaw a2a-gateway 发送消息（作为 client）
- [ ] 双向多轮对话正常工作
- [ ] AgentCard 包含完整的 securitySchemes + capabilities
- [ ] Task 状态转换合法，非法转换被拒绝

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| A2A v0.3↔v1.0 兼容层有 bug | 中 | 高 | 先用 curl 测试 v0.3 方法，再接 OpenClaw |
| OpenClaw SDK `@a2a-js/sdk ^0.3.13` 与 Python SDK 不完全兼容 | 低 | 高 | 查看 a2a-audit.jsonl 历史记录了解格式 |
| Hermes API Server 不支持某些 streaming 模式 | 低 | 中 | 先做非流式，流式作为 Phase 5 |
| 远程机器网络不稳定 | 中 | 低 | Tailscale 自动重连 |

---

## 技术决策记录

1. **v0.3 兼容**：使用 a2a-sdk 内置的 `enable_v0_3_compat=True`，而非自行实现 adapter
2. **REST transport**：使用 a2a-sdk 的 `create_rest_routes()`，无需自行实现
3. **Session 持久化**：SQLite（复用现有基础设施），Phase 5 迁移到 Redis
4. **A2A Client**：先用 a2a-sdk Python client 模块（如有），否则用 httpx 自行实现 JSON-RPC client
5. **不做 gRPC**：OpenClaw 的 gRPC transport 在 port+1，但我们不需要——JSON-RPC + REST 已足够
