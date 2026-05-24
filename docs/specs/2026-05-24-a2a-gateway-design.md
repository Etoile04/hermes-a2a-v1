# Hermes A2A v1 Gateway — Design Specification

**Date:** 2026-05-24
**Status:** Draft → Pending Review
**Author:** Hermes Agent (PM) + User (Wenjie Li)

---

## 1. Goal

Build a production-grade A2A v1.0 protocol gateway (`hermes-a2a-v1`) that enables two Hermes Agent instances (macOS + Linux over Tailscale) to discover each other, exchange tasks, and collaborate — using the standard A2A protocol backed by the official `a2a-sdk` Python library.

**Acceptance criteria:** User can send a message from Hermes Agent A (macOS, 192.168.3.200 / Tailscale) to Hermes Agent B (Linux, 100.70.30.21) via A2A, receive a response, and verify both sides log the interaction.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Machine A (macOS)                                       │
│                                                         │
│  Hermes Agent ──HTTP──► Hermes API Server (:8642)       │
│                              ↑                          │
│  hermes-a2a-v1 (:18800) ────┘                           │
│     ├── A2A JSON-RPC endpoint (/a2a/jsonrpc)            │
│     ├── Agent Card (/.well-known/agent-card.json)       │
│     ├── HermesTaskHandler → HermesClient → API Server   │
│     └── SQLite TaskStore                                │
│                                                         │
└─────────────── Tailscale (100.x.x.x) ──────────────────┘
                            │
                    A2A v1.0 JSON-RPC
                            │
┌─────────────────────────────────────────────────────────┐
│ Machine B (Linux, 100.70.30.21)                         │
│                                                         │
│  hermes-a2a-v1 (:18800) ──► Hermes API Server (:8642)   │
│     └── Same architecture as Machine A                  │
│                                                         │
│  Hermes Agent                                           │
└─────────────────────────────────────────────────────────┘
```

### Integration: A2A Gateway ↔ Hermes Agent

The gateway uses the **Hermes API Server** (OpenAI-compatible HTTP API on port 8642) as its backend:

| Gateway Action | Hermes API Call |
|---------------|-----------------|
| `on_message_send` | `POST /v1/chat/completions` with `X-Hermes-Session-Id` for multi-turn |
| `on_message_send_stream` | `POST /v1/chat/completions` with `stream: true` → SSE → A2A events |
| `on_cancel_task` | `POST /v1/runs/{run_id}/stop` or abort streaming |
| `on_get_task` | Read from TaskStore (local state) |

### Multi-turn Support

A2A `contextId` maps to Hermes `X-Hermes-Session-Id` header:
- First message: no contextId → create new Hermes session → store mapping
- Subsequent messages: use stored sessionId → continue conversation

---

## 3. Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | 3.13 (via `/opt/homebrew/bin/python3.13`) |
| A2A SDK | `a2a-sdk` | 1.0.3+ |
| Web Framework | FastAPI + uvicorn | Latest |
| SSE | `sse-starlette` | Latest |
| HTTP Client | `httpx` | Latest (async) |
| Task Persistence | SQLite via `aiosqlite` | Dev; Redis for prod |
| Config | YAML via `pyyaml` | Latest |
| Testing | `pytest` + `pytest-asyncio` | Latest |

---

## 4. Project Structure

```
hermes-a2a-v1/
├── pyproject.toml
├── README.md
├── config.example.yaml
├── src/
│   └── hermes_a2a/
│       ├── __init__.py
│       ├── server.py          # FastAPI app entry, route wiring
│       ├── handler.py         # HermesRequestHandler (a2a RequestHandler impl)
│       ├── hermes_client.py   # Async HTTP client to Hermes API Server
│       ├── task_store.py      # SQLite-backed TaskStore
│       ├── agent_card.py      # Agent Card builder from config
│       ├── config.py          # YAML config loader
│       ├── auth.py            # Bearer token auth middleware
│       └── models.py          # Internal Pydantic models for config/state
├── tests/
│   ├── conftest.py
│   ├── test_handler.py
│   ├── test_hermes_client.py
│   ├── test_task_store.py
│   ├── test_agent_card.py
│   └── test_integration.py
├── Dockerfile
└── docker-compose.yaml
```

---

## 5. Component Design

### 5.1 HermesRequestHandler (`handler.py`)

Implements `a2a.server.request_handlers.request_handler.RequestHandler`:

- `on_message_send`: Receive A2A message → extract text → call Hermes API → build A2A Task response
- `on_message_send_stream`: Same but with SSE streaming via AsyncGenerator
- `on_cancel_task`: Cancel Hermes run if possible, mark task CANCELED
- `on_get_task`: Return task from TaskStore
- `on_list_tasks`: Return filtered tasks from TaskStore

### 5.2 HermesClient (`hermes_client.py`)

Async HTTP client wrapping Hermes API Server:

```python
class HermesClient:
    async def send_message(self, text: str, session_id: str | None = None) -> tuple[str, str]
        # Returns (response_text, session_id)
        # POST /v1/chat/completions
        # Headers: X-Hermes-Session-Id for multi-turn
    
    async def send_message_stream(self, text: str, session_id: str | None = None) -> AsyncGenerator[str, None]
        # POST /v1/chat/completions with stream=true
        # Yields text chunks from SSE
    
    async def cancel_run(self, run_id: str) -> bool
        # POST /v1/runs/{run_id}/stop
    
    async def health_check(self) -> bool
        # GET /health
```

### 5.3 TaskStore (`task_store.py`)

SQLite-backed implementation of `a2a.server.tasks.task_store.TaskStore`:

- `save(task, context)` — Upsert task to SQLite
- `get(task_id, context)` — Read task by ID
- `delete(task_id, context)` — Remove task
- `list(params, context)` — Filtered/paginated list

Schema:
```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    context_id TEXT,
    state INTEGER,
    data TEXT NOT NULL,  -- JSON serialized task
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_tasks_context ON tasks(context_id);
CREATE INDEX idx_tasks_state ON tasks(state);
```

### 5.4 Agent Card (`agent_card.py`)

Builds A2A v1.0 Agent Card from config:

```json
{
    "name": "Hermes Agent - Machine A",
    "description": "AI Agent powered by Hermes via A2A v1.0",
    "url": "http://100.x.x.x:18800",
    "capabilities": {
        "streaming": true,
        "pushNotifications": false
    },
    "skills": [
        {"id": "general", "name": "General Q&A", "description": "General question answering"},
        {"id": "coding", "name": "Coding Assistant", "description": "Code generation and debugging"},
        {"id": "research", "name": "Research", "description": "Web search and research"}
    ],
    "authentication": {"schemes": ["bearer"]}
}
```

### 5.5 Config (`config.py`)

```yaml
server:
  host: "0.0.0.0"
  port: 18800

hermes:
  api_url: "http://localhost:8642"
  timeout: 300

agent:
  name: "Hermes Agent"
  description: "AI Agent powered by Hermes"
  url: "http://100.x.x.x:18800"
  skills:
    - id: general
      name: General Q&A
      description: General question answering

auth:
  enabled: true
  token: ""  # generate with: openssl rand -hex 24

task_store:
  type: sqlite
  path: "~/.hermes/a2a-gateway/tasks.db"

logging:
  level: INFO
```

---

## 6. Deployment

### Machine A (macOS, this machine)
- Port 18800 for A2A gateway
- Port 8642 for Hermes API Server (must be enabled)
- Tailscale IP for cross-machine access

### Machine B (Linux, 100.70.30.21)
- Same setup as Machine A
- Configure Machine A as peer

### Network
- Tailscale VPN for secure cross-machine connectivity
- Port 18800 must be accessible between machines

---

## 7. Testing Strategy

| Level | What | Tools |
|-------|------|-------|
| Unit | Each component in isolation | pytest + mocking (respx for HTTP) |
| Integration | Handler + HermesClient + TaskStore | pytest-asyncio |
| E2E | Two gateway instances communicating | Manual + curl scripts |
| Acceptance | Cross-machine A2A message round-trip | Manual verification |

---

## 8. Out of Scope (for now)

- Redis TaskStore (production optimization)
- Multi-tenant routing
- Dashboard UI
- OAuth2/mTLS auth (Bearer token only for now)
- Push notifications
- Docker packaging
