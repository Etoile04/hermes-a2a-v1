# hermes-a2a-v1: A2A v1.0 Gateway for Hermes Agent — Design Spec

**Date:** 2026-05-24
**Status:** Draft
**Author:** Hermes Agent (PM) + Wenjie Li

---

## 1. Goal

Build a production-ready A2A v1.0 protocol gateway that exposes Hermes Agent as an A2A-compliant remote agent, enabling any A2A v1.0 client (from any framework, any host) to discover, communicate with, and delegate tasks to Hermes Agent — with full support for multi-turn conversations, async execution, SSE streaming, and enterprise-grade reliability.

## 2. Architecture

### 2.1 High-Level

```
┌─────────────────┐    A2A v1.0      ┌───────────────────────┐     HTTP      ┌──────────────┐
│  Remote Agent   │◄──JSON-RPC/SSE──►│  hermes-a2a-v1        │◄─────────────►│ Hermes Agent │
│  (any A2A impl) │                  │  (a2a-sdk based)      │               │  API Server  │
└─────────────────┘                  └───────────────────────┘               └──────────────┘
```

The gateway is a **protocol bridge**: it speaks A2A v1.0 on the left, Hermes API Server HTTP on the right.

### 2.2 Component Diagram

```
                         A2A Protocol v1.0
                               │
                    ┌──────────▼──────────┐
                    │  a2a-sdk A2AServer   │
                    │                      │
                    │  ┌────────────────┐  │
                    │  │ HermesHandler  │  │  implements a2a-sdk TaskHandler
                    │  │  ┌───────────┐ │  │
                    │  │  │TaskManager│ │  │  state machine + context mgmt
                    │  │  └─────┬─────┘ │  │
                    │  │  ┌─────▼─────┐ │  │
                    │  │  │HermesClient│ │  │  async HTTP → Hermes API Server
                    │  │  └───────────┘ │  │
                    │  └────────────────┘  │
                    │                      │
                    │  ┌────────────────┐  │
                    │  │  TaskStore     │  │  SQLite (dev) / Redis (prod)
                    │  └────────────────┘  │
                    │                      │
                    │  ┌────────────────┐  │
                    │  │  AuthService   │  │  Bearer Token / OAuth 2.0
                    │  └────────────────┘  │
                    └──────────────────────┘
```

## 3. Components

### 3.1 HermesTaskHandler

Implements a2a-sdk's `TaskHandler` interface. Routes A2A protocol methods to Hermes Agent.

**Methods:**

| Method | Behavior |
|--------|----------|
| `on_task_create(task, message)` | Create new task → send message to Hermes API → return `WORKING` (async) or `INPUT_REQUIRED` |
| `on_task_update(task, message)` | Multi-turn: inject conversation history + new message into Hermes → update task |
| `on_stream(task, message)` | SSE bridge: stream tokens from Hermes API → emit A2A `StreamResponse` events |
| `on_cancel(task)` | Cancel ongoing Hermes session → set task to `CANCELED` |

**Multi-turn context injection:**
- Each Task stores a `session_id` mapping to Hermes Agent's conversation session
- On `on_task_update`, the handler replays context via Hermes session continuity (not re-sending full history)
- Fallback: if session expired, reconstruct from stored `messages[]` array

### 3.2 HermesClient

Async HTTP client for Hermes Agent API Server.

**Methods:**

| Method | Description |
|--------|-------------|
| `send_message(text, session_id=None) → HermesResponse` | `POST /v1/chat/completions` (non-streaming) with `X-Hermes-Session-Id` |
| `stream_message(text, session_id=None) → AsyncIterator[str]` | `POST /v1/chat/completions` with `stream: true`, yields SSE `data:` lines |
| `start_run(text) → str` | `POST /v1/runs` → returns `run_id` for async execution |
| `get_run_status(run_id) → RunStatus` | `GET /v1/runs/{run_id}` |
| `stream_run_events(run_id) → AsyncIterator[Event]` | `GET /v1/runs/{run_id}/events` — SSE lifecycle events |
| `cancel_run(run_id) → bool` | `POST /v1/runs/{run_id}/stop` |
| `health_check() → bool` | `GET /health` |

**Implementation:** `httpx.AsyncClient` with connection pooling, retries, and timeout config.

**Hermes API Server endpoints** (OpenAI-compatible, default `http://localhost:8642`):
- `POST /v1/chat/completions` — OpenAI Chat Completions format (stateless; session continuity via `X-Hermes-Session-Id` header; supports `stream: true` for SSE)
- `POST /v1/responses` — OpenAI Responses API format (stateful via `previous_response_id`)
- `POST /v1/runs` — async: start a run, returns `run_id` immediately (HTTP 202)
- `GET /v1/runs/{run_id}` — retrieve run status
- `GET /v1/runs/{run_id}/events` — SSE stream of structured lifecycle events
- `POST /v1/runs/{run_id}/stop` — interrupt a running agent
- `GET /health` — health check

**Primary integration path:** `POST /v1/chat/completions` with `stream: true` + `X-Hermes-Session-Id` header for session continuity. This provides both synchronous and streaming modes through a single endpoint.

### 3.3 TaskManager

Manages A2A Task lifecycle and conversation context.

**State machine:**

```
SUBMITTED ──► WORKING ──► COMPLETED
                  │             │
                  ▼             ▼
           INPUT_REQUIRED   FAILED
                  │
                  ▼
              REJECTED
              
         (any non-terminal) ──► CANCELED
```

**State transitions (allowed):**

| From | To |
|------|----|
| SUBMITTED | WORKING, FAILED, REJECTED, CANCELED |
| WORKING | COMPLETED, FAILED, INPUT_REQUIRED, CANCELED |
| INPUT_REQUIRED | WORKING, FAILED, REJECTED, CANCELED |
| COMPLETED | *(terminal)* |
| FAILED | *(terminal)* |
| CANCELED | *(terminal)* |
| REJECTED | *(terminal)* |

**Invalid transitions** raise `TaskStateError`.

**Message history:**
- Each Task maintains `messages: list[Message]` (user + agent)
- Messages are appended on each interaction
- Full history available for context reconstruction

**Context grouping:**
- `context_id` links related tasks into a conversation thread
- All tasks with same `context_id` share a Hermes session

### 3.4 TaskStore

Abstract interface for task persistence.

```python
class BaseTaskStore(ABC):
    async def save(self, task: Task) -> None: ...
    async def load(self, task_id: str) -> Task | None: ...
    async def delete(self, task_id: str) -> None: ...
    async def list_by_context(self, context_id: str) -> list[Task]: ...
    async def cleanup_expired(self, ttl_hours: int = 72) -> int: ...
```

**Implementations:**

1. **SQLiteTaskStore** — for development / single-instance deployment
   - File: `~/.hermes/a2a-v1/tasks.db`
   - Uses `aiosqlite` for async access
   - Auto-creates table on first use

2. **RedisTaskStore** — for production / multi-instance deployment
   - Key pattern: `a2a:task:{task_id}`
   - Context index: `a2a:context:{context_id}` → set of task_ids
   - TTL-based expiry via Redis EXPIRE

**Configuration:** `task_store.backend: sqlite | redis`

### 3.5 AuthService

Authentication middleware for incoming A2A requests.

**Supported schemes:**

| Scheme | Config | Use Case |
|--------|--------|----------|
| Bearer Token | `auth.tokens: [token1, token2]` | Simple / development |
| OAuth 2.0 | `auth.oauth.jwks_url`, `auth.oauth.issuer` | Enterprise |

**Behavior:**
- Tokens declared in Agent Card `security_schemes`
- Middleware validates token before routing to handler
- Unauthenticated requests return JSON-RPC error `-32600`

### 3.6 Config

YAML-based configuration file.

**Location:** `~/.hermes/a2a-v1/config.yaml` (overridable via `--config` flag)

**Structure:**

```yaml
server:
  host: "0.0.0.0"
  port: 18800
  workers: 4                # uvicorn workers

agent:
  name: "hermes-agent"
  description: "Hermes Agent exposed via A2A v1.0"
  version: "1.0.0"
  url: "http://localhost:18800"
  skills:                   # exposed as AgentCard skills
    - id: "general-chat"
      name: "General Chat"
      description: "General purpose AI assistant"
      tags: ["chat", "assistant"]

hermes:
  api_url: "http://localhost:8642"     # Hermes API Server (OpenAI-compatible)
  timeout: 300
  default_profile: null               # optional: Hermes profile to use

task_store:
  backend: "sqlite"       # "sqlite" | "redis"
  sqlite_path: "~/.hermes/a2a-v1/tasks.db"
  redis_url: "redis://localhost:6379/0"
  ttl_hours: 72

auth:
  enabled: true
  tokens:
    - "CHANGE_ME_GENERATE_WITH_OPENSSL"
  oauth:
    enabled: false
    jwks_url: ""
    issuer: ""

logging:
  level: "INFO"
  format: "json"           # "json" | "text"
```

## 4. Data Flow

### 4.1 Simple Request (non-streaming)

```
Remote Agent                          hermes-a2a-v1                         Hermes API
    │                                      │                                    │
    │  SendMessage(task_id, message)       │                                    │
    ├─────────────────────────────────────►│                                    │
    │                                      │  POST /chat {message, session_id}  │
    │                                      ├───────────────────────────────────►│
    │                                      │                                    │
    │                                      │  {response, session_id}            │
    │  Task{WORKING → COMPLETED}           │◄───────────────────────────────────┤
    │◄─────────────────────────────────────│                                    │
    │                                      │                                    │
```

### 4.2 Multi-turn Conversation

```
Remote Agent                          hermes-a2a-v1                         Hermes API
    │                                      │                                    │
    │  SendMessage(t-1, "订机票")          │                                    │
    ├─────────────────────────────────────►│                                    │
    │                                      │  POST /chat {message}              │
    │                                      ├───────────────────────────────────►│
    │  Task{INPUT_REQUIRED}                │  "需要什么日期？"                    │
    │◄─────────────────────────────────────│◄───────────────────────────────────┤
    │                                      │                                    │
    │  SendMessage(t-1, "6月15日")         │                                    │
    ├─────────────────────────────────────►│                                    │
    │                                      │  POST /chat {message, session_id} │
    │                                      ├───────────────────────────────────►│
    │  Task{COMPLETED, artifact}           │  "已预订..."                       │
    │◄─────────────────────────────────────│◄───────────────────────────────────┤
    │                                      │                                    │
```

### 4.3 Streaming (SSE)

```
Remote Agent                          hermes-a2a-v1                         Hermes API
    │                                      │                                    │
    │  SendStreamingMessage(t-2, msg)      │                                    │
    ├─────────────────────────────────────►│                                    │
    │                                      │  GET /chat/stream {message}        │
    │                                      ├───────────────────────────────────►│
    │  SSE: {message: "正"}                │  SSE: "正"                         │
    │◄─────────────────────────────────────│◄───────────────────────────────────┤
    │  SSE: {message: "在"}                │  SSE: "在"                         │
    │◄─────────────────────────────────────│◄───────────────────────────────────┤
    │  SSE: {artifact, lastChunk: true}    │  SSE: "分析..."                    │
    │◄─────────────────────────────────────│◄───────────────────────────────────┤
    │  SSE: {done}                         │                                    │
    │◄─────────────────────────────────────│                                    │
```

## 5. Error Handling

| Scenario | Gateway Behavior |
|----------|------------------|
| Hermes API unreachable | Task → `FAILED` with error message. Retry with exponential backoff (3 attempts). |
| Hermes API timeout (>300s) | Task → `FAILED` with timeout message. |
| Invalid A2A request | Return JSON-RPC error `-32600` (Invalid Request). |
| Task not found | Return JSON-RPC error `-32001` (Task not found). |
| Invalid state transition | Return JSON-RPC error `-32002` (Invalid state transition). |
| Auth failure | Return JSON-RPC error `-32600` with auth error detail. |
| Rate limit | Return HTTP 429 with retry-after header. |

## 6. Multi-instance Deployment

```
                    ┌──── Nginx / HAProxy ────┐
                    │     (load balancer)      │
                    └───────┬──────┬──────────┘
                            │      │
                   ┌────────▼┐  ┌──▼────────┐
                   │Instance1│  │ Instance2  │
                   │ a2a-v1  │  │  a2a-v1    │
                   └────┬────┘  └─────┬──────┘
                        │             │
                        └──────┬──────┘
                               │
                         ┌─────▼─────┐
                         │   Redis   │   shared task state
                         └───────────┘
                               │
                         ┌─────▼─────┐
                         │  Hermes   │   can be shared or per-instance
                         │  API      │
                         └───────────┘
```

**Requirements for multi-instance:**
1. `task_store.backend: "redis"` — shared state
2. Sticky sessions NOT required — any instance can handle any Task's continuation
3. Agent Card URL points to load balancer

## 7. Testing Strategy

### Unit Tests (per component)
- `test_task_manager.py` — state machine transitions, invalid transitions, message history
- `test_hermes_client.py` — mocked HTTP responses, retry logic, timeout handling
- `test_task_store.py` — save/load/delete/cleanup for both SQLite and Redis
- `test_auth.py` — token validation, expired tokens, missing tokens
- `test_config.py` — config loading, defaults, validation

### Integration Tests
- `test_handler.py` — full TaskHandler flow with mocked Hermes API
- `test_integration.py` — end-to-end A2A client → gateway → mocked Hermes

### Test Approach
- TDD: write failing test first, then implement
- Use `pytest-asyncio` for async test support
- Mock Hermes API with `respx` (httpx mocking library)
- Target: >90% code coverage

## 8. Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| A2A Protocol SDK | `a2a-sdk` | >=1.0.3 |
| HTTP Framework | FastAPI (via a2a-sdk) | >=0.115 |
| Async HTTP Client | `httpx` | >=0.28 |
| Task Storage (dev) | `aiosqlite` | >=0.20 |
| Task Storage (prod) | `redis` (via `redis[hiredis]`) | >=5.0 |
| Auth | `PyJWT` | >=2.8 |
| Config | `pyyaml` | >=6.0 |
| Testing | `pytest`, `pytest-asyncio`, `respx` | latest |
| Python | | >=3.10 |
| Container | Docker + docker-compose | |
| CI | GitHub Actions | |

## 9. File Structure

```
hermes-a2a-v1/
├── pyproject.toml
├── README.md
├── README_zh.md
├── Dockerfile
├── docker-compose.yaml
├── config.example.yaml
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── src/
│   └── hermes_a2a/
│       ├── __init__.py
│       ├── __main__.py           # python -m hermes_a2a
│       ├── server.py             # A2AServer setup + uvicorn launch
│       ├── handler.py            # HermesTaskHandler
│       ├── hermes_client.py      # Hermes API async client
│       ├── task_manager.py       # Task state machine + context
│       ├── task_store.py         # BaseTaskStore + SQLite + Redis impls
│       ├── config.py             # YAML config loader + validator
│       ├── auth.py               # Authentication service
│       ├── agent_card.py         # Agent Card builder from config
│       └── models.py             # Pydantic models for internal types
├── tests/
│   ├── conftest.py               # shared fixtures
│   ├── test_task_manager.py
│   ├── test_hermes_client.py
│   ├── test_task_store.py
│   ├── test_auth.py
│   ├── test_config.py
│   ├── test_handler.py
│   └── test_integration.py
└── docs/
    ├── superpowers/
    │   ├── specs/
    │   │   └── 2026-05-24-a2a-v1-gateway-design.md  (this file)
    │   └── plans/
    └── deployment.md
```

## 10. Non-Goals (YAGNI)

These are explicitly out of scope for v1.0:

- ❌ Agent-to-Agent orchestration (this is a gateway, not an orchestrator)
- ❌ Built-in LLM inference (delegated to Hermes Agent)
- ❌ gRPC protocol binding (JSON-RPC/SSE only for v1.0)
- ❌ Webhook push notifications (polling + streaming only)
- ❌ Admin UI / dashboard
- ❌ Multi-tenant agent hosting (single agent per gateway instance)

## 11. Success Criteria

1. **A2A v1.0 Compliance** — Passes official A2A conformance tests
2. **Multi-turn** — Consecutive messages maintain conversation context via Hermes session
3. **Streaming** — Token-by-token SSE streaming from Hermes to remote agent
4. **Async** — Non-blocking: `SendMessage` returns immediately, results via polling or streaming
5. **Multi-instance** — Two instances behind load balancer can share task state via Redis
6. **Test Coverage** — >90% code coverage, all tests passing
7. **Docker** — One-command deployment: `docker-compose up`
