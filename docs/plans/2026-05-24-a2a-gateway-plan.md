# Hermes A2A v1 Gateway — Implementation Plan

> **For Hermes:** Use `software-development/subagent-driven-development` skill to implement this plan task-by-task.

**Goal:** Build a working A2A v1.0 gateway that enables two Hermes Agent instances to communicate over Tailscale.

**Architecture:** A2A v1.0 gateway (FastAPI + a2a-sdk) bridging to Hermes API Server (HTTP). SQLite TaskStore. Multi-turn via contextId → sessionId mapping.

**Tech Stack:** Python 3.13, a2a-sdk 1.0.3, FastAPI, uvicorn, httpx, aiosqlite, sse-starlette, pyyaml

---

## Phase 1: Project Foundation + Core Gateway (本地可运行)

**Milestone:** Gateway starts, serves Agent Card, accepts A2A messages, and forwards them to a local Hermes Agent, returning responses. Single-machine end-to-end working.

### Task 1.1: Create project scaffold

**Objective:** Set up project structure, venv, dependencies, pyproject.toml

**Files:**
- Create: `~/projects/hermes-a2a-v1/pyproject.toml`
- Create: `~/projects/hermes-a2a-v1/src/hermes_a2a/__init__.py`
- Create: `~/projects/hermes-a2a-v1/config.example.yaml`
- Create: `~/projects/hermes-a2a-v1/.gitignore`

**Step 1: Create project directory structure**
```bash
cd ~/projects/hermes-a2a-v1
mkdir -p src/hermes_a2a tests docs
```

**Step 2: Create pyproject.toml**
```toml
[project]
name = "hermes-a2a-v1"
version = "0.1.0"
description = "A2A v1.0 protocol gateway for Hermes Agent"
requires-python = ">=3.10"
dependencies = [
    "a2a-sdk>=1.0.3",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "httpx>=0.28.0",
    "sse-starlette>=2.0.0",
    "aiosqlite>=0.20.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.22",
]

[project.scripts]
hermes-a2a = "hermes_a2a.server:main"
```

**Step 3: Create venv and install deps**
```bash
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Step 4: Verify imports work**
```bash
python -c "from a2a.server.request_handlers.request_handler import RequestHandler; print('a2a-sdk OK')"
python -c "import fastapi; print('fastapi OK')"
python -c "import httpx; print('httpx OK')"
```

**Step 5: Init git and commit**
```bash
git init
git add -A
git commit -m "chore: project scaffold with dependencies"
```

---

### Task 1.2: Implement config loader

**Objective:** YAML config loading with defaults and validation

**Files:**
- Create: `src/hermes_a2a/config.py`
- Create: `src/hermes_a2a/models.py`
- Test: `tests/test_config.py`

**Step 1: Write failing test for config loading**
```python
# tests/test_config.py
import pytest
from hermes_a2a.config import load_config

def test_load_default_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  port: 9999\n")
    cfg = load_config(str(config_path))
    assert cfg.server.port == 9999
    assert cfg.server.host == "0.0.0.0"  # default
    assert cfg.hermes.api_url == "http://localhost:8642"  # default

def test_load_missing_file_uses_defaults():
    cfg = load_config("/nonexistent/config.yaml")
    assert cfg.server.port == 18800
```

**Step 2: Run test to verify failure**
Run: `cd ~/projects/hermes-a2a-v1 && source .venv/bin/activate && pytest tests/test_config.py -v`
Expected: FAIL — module not found

**Step 3: Implement config.py and models.py**

`models.py` — Pydantic models for config:
```python
from pydantic import BaseModel

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 18800

class HermesConfig(BaseModel):
    api_url: str = "http://localhost:8642"
    timeout: int = 300

class AgentSkillConfig(BaseModel):
    id: str = "general"
    name: str = "General"
    description: str = ""

class AgentConfig(BaseModel):
    name: str = "Hermes Agent"
    description: str = "AI Agent powered by Hermes via A2A v1.0"
    url: str = "http://localhost:18800"
    skills: list[AgentSkillConfig] = []

class AuthConfig(BaseModel):
    enabled: bool = True
    token: str = ""

class TaskStoreConfig(BaseModel):
    type: str = "sqlite"
    path: str = "~/.hermes/a2a-gateway/tasks.db"

class GatewayConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    hermes: HermesConfig = HermesConfig()
    agent: AgentConfig = AgentConfig()
    auth: AuthConfig = AuthConfig()
    task_store: TaskStoreConfig = TaskStoreConfig()
    logging_level: str = "INFO"
```

`config.py` — YAML loader:
```python
import yaml
import os
from pathlib import Path
from hermes_a2a.models import GatewayConfig

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.hermes/a2a-gateway/config.yaml")

def load_config(path: str | None = None) -> GatewayConfig:
    if path and Path(path).exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    return GatewayConfig(**data)
```

**Step 4: Run tests**
Run: `pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add -A && git commit -m "feat: add config loader with Pydantic models"
```

---

### Task 1.3: Implement SQLite TaskStore

**Objective:** A2A TaskStore interface backed by SQLite

**Files:**
- Create: `src/hermes_a2a/task_store.py`
- Test: `tests/test_task_store.py`

**Step 1: Write failing tests for TaskStore CRUD**
```python
# tests/test_task_store.py
import pytest
from hermes_a2a.task_store import SQLiteTaskStore

@pytest.fixture
async def store(tmp_path):
    s = SQLiteTaskStore(str(tmp_path / "test.db"))
    await s.init()
    yield s
    await s.close()

@pytest.mark.asyncio
async def test_save_and_get(store):
    task = {"id": "t1", "status": {"state": "WORKING"}}
    await store.save(task, None)
    result = await store.get("t1", None)
    assert result is not None
    assert result["id"] == "t1"

@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    result = await store.get("nonexistent", None)
    assert result is None

@pytest.mark.asyncio
async def test_delete(store):
    task = {"id": "t2", "status": {"state": "COMPLETED"}}
    await store.save(task, None)
    await store.delete("t2", None)
    result = await store.get("t2", None)
    assert result is None

@pytest.mark.asyncio
async def test_list(store):
    for i in range(5):
        await store.save({"id": f"t{i}", "status": {"state": "COMPLETED"}}, None)
    results = await store.list(None, None)
    assert len(results) == 5
```

**Step 2: Run tests to verify failure**

**Step 3: Implement SQLiteTaskStore**

```python
# src/hermes_a2a/task_store.py
import json
import aiosqlite
import os
from pathlib import Path

class SQLiteTaskStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                context_id TEXT,
                state INTEGER DEFAULT 1,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_context ON tasks(context_id)")
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    async def save(self, task, context) -> None:
        task_id = task.get("id")
        context_id = task.get("contextId")
        state = task.get("status", {}).get("state", 1)
        data = json.dumps(task)
        await self._db.execute(
            """INSERT OR REPLACE INTO tasks (task_id, context_id, state, data, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (task_id, context_id, state, data)
        )
        await self._db.commit()

    async def get(self, task_id: str, context) -> dict | None:
        async with self._db.execute("SELECT data FROM tasks WHERE task_id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else None

    async def delete(self, task_id: str, context) -> None:
        await self._db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        await self._db.commit()

    async def list(self, params, context) -> list:
        async with self._db.execute("SELECT data FROM tasks ORDER BY updated_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [json.loads(r[0]) for r in rows]
```

**Step 4: Run tests**
Run: `pytest tests/test_task_store.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add -A && git commit -m "feat: add SQLite TaskStore with CRUD operations"
```

---

### Task 1.4: Implement HermesClient

**Objective:** Async HTTP client to communicate with Hermes API Server

**Files:**
- Create: `src/hermes_a2a/hermes_client.py`
- Test: `tests/test_hermes_client.py`

**Step 1: Write failing tests (mocked HTTP)**
```python
# tests/test_hermes_client.py
import pytest
import httpx
import respx
from hermes_a2a.hermes_client import HermesClient

@pytest.fixture
def client():
    return HermesClient(base_url="http://localhost:8642", timeout=30)

@pytest.mark.asyncio
@respx.mock
async def test_send_message(client):
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hello from Hermes!"}}],
        })
    )
    text, session_id = await client.send_message("Hello")
    assert text == "Hello from Hermes!"
    assert session_id is not None  # new session created

@pytest.mark.asyncio
@respx.mock
async def test_send_message_multi_turn(client):
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "chatcmpl-456",
            "choices": [{"message": {"content": "Follow-up response"}}],
            "session_id": "sess-abc"
        })
    )
    text, session_id = await client.send_message("Follow up", session_id="sess-abc")
    assert text == "Follow-up response"
```

**Step 2: Run tests to verify failure**

**Step 3: Implement HermesClient**

```python
# src/hermes_a2a/hermes_client.py
import httpx
import uuid

class HermesClient:
    def __init__(self, base_url: str = "http://localhost:8642", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def send_message(self, text: str, session_id: str | None = None) -> tuple[str, str]:
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": text}],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            response_text = data["choices"][0]["message"]["content"]
            new_session_id = data.get("session_id", session_id or str(uuid.uuid4()))
            return response_text, new_session_id

    async def send_message_stream(self, text: str, session_id: str | None = None):
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": text}],
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        import json
                        chunk = json.loads(line[6:])
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False
```

**Step 4: Run tests**
Run: `pytest tests/test_hermes_client.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add -A && git commit -m "feat: add HermesClient for API Server communication"
```

---

### Task 1.5: Implement HermesRequestHandler

**Objective:** A2A RequestHandler that wires TaskStore + HermesClient together

**Files:**
- Create: `src/hermes_a2a/handler.py`
- Test: `tests/test_handler.py`

**Step 1: Write failing tests**
```python
# tests/test_handler.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from hermes_a2a.handler import HermesRequestHandler

@pytest.fixture
def handler():
    hermes_client = AsyncMock()
    hermes_client.send_message = AsyncMock(return_value=("Response text", "sess-1"))
    task_store = AsyncMock()
    task_store.save = AsyncMock()
    task_store.get = AsyncMock(return_value=None)
    return HermesRequestHandler(hermes_client=hermes_client, task_store=task_store)

@pytest.mark.asyncio
async def test_on_message_send(handler):
    params = MagicMock()
    params.message = MagicMock()
    params.message.parts = [MagicMock()]
    params.message.parts[0].text = "Hello Hermes"
    context = MagicMock()

    result = await handler.on_message_send(params, context)
    # Should return a Task or Message with the response
    assert result is not None
    handler.hermes_client.send_message.assert_called_once()
```

**Step 2: Run tests to verify failure**

**Step 3: Implement handler**

```python
# src/hermes_a2a/handler.py
import uuid
import json
from typing import AsyncGenerator
from a2a.server.request_handlers.request_handler import RequestHandler

class HermesRequestHandler(RequestHandler):
    def __init__(self, hermes_client, task_store, session_map: dict | None = None):
        self.hermes_client = hermes_client
        self.task_store = task_store
        self.session_map = session_map or {}  # context_id → hermes session_id

    def _extract_text(self, params) -> str:
        """Extract text content from A2A message parts."""
        if hasattr(params, 'message') and params.message:
            parts = getattr(params.message, 'parts', [])
            texts = []
            for part in parts:
                if hasattr(part, 'text'):
                    texts.append(part.text)
            return "\n".join(texts)
        return ""

    async def on_message_send(self, params, context):
        text = self._extract_text(params)
        context_id = getattr(params, 'contextId', None) or str(uuid.uuid4())

        # Get or create Hermes session
        session_id = self.session_map.get(context_id)

        # Call Hermes
        response_text, new_session_id = await self.hermes_client.send_message(text, session_id)
        self.session_map[context_id] = new_session_id

        # Build task
        task = {
            "id": str(uuid.uuid4()),
            "contextId": context_id,
            "status": {"state": "completed"},
            "artifacts": [
                {"parts": [{"type": "text", "text": response_text}]}
            ],
            "history": [
                {"role": "user", "parts": [{"type": "text", "text": text}]},
                {"role": "agent", "parts": [{"type": "text", "text": response_text}]},
            ]
        }

        await self.task_store.save(task, context)
        return task

    async def on_message_send_stream(self, params, context) -> AsyncGenerator:
        text = self._extract_text(params)
        context_id = getattr(params, 'contextId', None) or str(uuid.uuid4())
        session_id = self.session_map.get(context_id)
        task_id = str(uuid.uuid4())

        # Yield working status
        yield {
            "type": "status_update",
            "task_id": task_id,
            "status": {"state": "working"},
        }

        # Stream from Hermes
        full_text = []
        async for chunk in self.hermes_client.send_message_stream(text, session_id):
            full_text.append(chunk)
            yield {
                "type": "artifact_update",
                "task_id": task_id,
                "artifact": {"parts": [{"type": "text", "text": chunk}]},
            }

        # Save completed task
        response_text = "".join(full_text)
        task = {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"type": "text", "text": response_text}]}],
        }
        await self.task_store.save(task, context)
        yield task

    async def on_get_task(self, params, context):
        task_id = getattr(params, 'id', None)
        return await self.task_store.get(task_id, context)

    async def on_cancel_task(self, params, context):
        task_id = getattr(params, 'id', None)
        task = await self.task_store.get(task_id, context)
        if task:
            task["status"]["state"] = "canceled"
            await self.task_store.save(task, context)
        return task

    async def on_list_tasks(self, params, context):
        tasks = await self.task_store.list(params, context)
        return {"tasks": tasks}
```

**Step 4: Run tests**
Run: `pytest tests/test_handler.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add -A && git commit -m "feat: add HermesRequestHandler with sync and stream modes"
```

---

### Task 1.6: Implement Agent Card builder

**Objective:** Build A2A v1.0 compliant Agent Card from config

**Files:**
- Create: `src/hermes_a2a/agent_card.py`
- Test: `tests/test_agent_card.py`

**Step 1: Write failing test**
```python
# tests/test_agent_card.py
from hermes_a2a.agent_card import build_agent_card
from hermes_a2a.models import GatewayConfig, AgentConfig, AgentSkillConfig

def test_build_agent_card():
    cfg = GatewayConfig(
        agent=AgentConfig(
            name="Test Agent",
            description="Test",
            url="http://localhost:18800",
            skills=[AgentSkillConfig(id="coding", name="Coding", description="Code help")]
        )
    )
    card = build_agent_card(cfg)
    assert card["name"] == "Test Agent"
    assert card["url"] == "http://localhost:18800"
    assert len(card["skills"]) == 1
    assert "streaming" in card["capabilities"]
```

**Step 2: Run tests to verify failure**

**Step 3: Implement agent_card.py**
```python
def build_agent_card(config) -> dict:
    return {
        "name": config.agent.name,
        "description": config.agent.description,
        "url": config.agent.url,
        "version": "0.1.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
        "skills": [
            {"id": s.id, "name": s.name, "description": s.description}
            for s in config.agent.skills
        ] or [{"id": "general", "name": "General", "description": "General assistance"}],
        "authentication": {"schemes": ["bearer"]} if config.auth.enabled else {},
    }
```

**Step 4: Run tests**
Run: `pytest tests/test_agent_card.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add -A && git commit -m "feat: add Agent Card builder"
```

---

### Task 1.7: Wire FastAPI server with all components

**Objective:** Entry point that wires routes, handler, task store, and agent card

**Files:**
- Create: `src/hermes_a2a/server.py`
- Create: `src/hermes_a2a/auth.py`

**Implementation:**

`auth.py`:
```python
from fastapi import Request, HTTPException

class BearerAuth:
    def __init__(self, token: str):
        self.token = token

    async def __call__(self, request: Request):
        if not self.token:
            return  # auth disabled
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.token}":
            raise HTTPException(status_code=401, detail="Unauthorized")
```

`server.py`:
```python
import uvicorn
from fastapi import FastAPI
from a2a.server.routes import create_jsonrpc_routes
from hermes_a2a.config import load_config
from hermes_a2a.handler import HermesRequestHandler
from hermes_a2a.hermes_client import HermesClient
from hermes_a2a.task_store import SQLiteTaskStore
from hermes_a2a.agent_card import build_agent_card

def create_app(config_path: str | None = None):
    config = load_config(config_path)

    app = FastAPI(title="Hermes A2A Gateway", version="0.1.0")

    # Init components
    task_store = SQLiteTaskStore(config.task_store.path)
    hermes_client = HermesClient(config.hermes.api_url, config.hermes.timeout)
    handler = HermesRequestHandler(hermes_client, task_store)

    # Agent Card endpoint
    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return build_agent_card(config)

    # Health check
    @app.get("/health")
    async def health():
        hermes_ok = await hermes_client.health_check()
        return {"status": "ok" if hermes_ok else "degraded", "hermes": hermes_ok}

    # Startup/shutdown
    @app.on_event("startup")
    async def startup():
        await task_store.init()

    @app.on_event("shutdown")
    async def shutdown():
        await task_store.close()

    # Wire A2A JSON-RPC routes
    routes = create_jsonrpc_routes(handler, rpc_url="/a2a/jsonrpc")
    for route in routes:
        app.router.routes.append(route)

    return app

def main():
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    config = load_config(config_path)
    app = create_app(config_path)
    uvicorn.run(app, host=config.server.host, port=config.server.port)

if __name__ == "__main__":
    main()
```

**Step 1: Test server starts**
```bash
cd ~/projects/hermes-a2a-v1
source .venv/bin/activate
python -m hermes_a2a.server &
sleep 2
curl http://localhost:18800/health
curl http://localhost:18800/.well-known/agent-card.json | python -m json.tool
kill %1
```

**Step 2: Commit**
```bash
git add -A && git commit -m "feat: add FastAPI server with A2A routes and agent card"
```

---

### Task 1.8: Create config.example.yaml and test full local loop

**Objective:** Config file for this machine, test message round-trip

**Files:**
- Create: `config.example.yaml`
- Create: `scripts/test_local.sh`

**Step 1: Create example config**
```yaml
# config.example.yaml
server:
  host: "0.0.0.0"
  port: 18800

hermes:
  api_url: "http://localhost:8642"
  timeout: 300

agent:
  name: "Hermes Agent"
  description: "AI Agent powered by Hermes via A2A v1.0"
  url: "http://localhost:18800"
  skills:
    - id: general
      name: General Q&A
      description: General question answering and conversation

auth:
  enabled: false
  token: ""

task_store:
  type: sqlite
  path: "~/.hermes/a2a-gateway/tasks.db"

logging:
  level: INFO
```

**Step 2: Write test script**
```bash
#!/bin/bash
# scripts/test_local.sh — Test local A2A round-trip
set -e
echo "Starting gateway..."
python -m hermes_a2a.server config.yaml &
PID=$!
sleep 3

echo "Testing health..."
curl -s http://localhost:18800/health | python -m json.tool

echo "Testing Agent Card..."
curl -s http://localhost:18800/.well-known/agent-card.json | python -m json.tool

echo "Testing JSON-RPC message..."
curl -s -X POST http://localhost:18800/a2a/jsonrpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "test-001",
        "role": "user",
        "parts": [{"type": "text", "text": "Hello from A2A!"}]
      }
    },
    "id": 1
  }' | python -m json.tool

echo "Stopping gateway..."
kill $PID
echo "Done!"
```

**Step 3: Commit**
```bash
git add -A && git commit -m "feat: add example config and local test script"
```

---

## Phase 1 Verification

After all Phase 1 tasks:
1. `pytest tests/ -v` — all unit tests pass
2. Start gateway → health check returns ok
3. Agent Card endpoint returns valid JSON
4. JSON-RPC message/send returns a Task with Hermes response
5. Multi-turn: second message with contextId continues conversation

---

## Phase 2: Cross-Machine Deployment + Acceptance

### Task 2.1: Create machine-specific configs

Machine A (macOS, this machine):
```yaml
agent:
  name: "Hermes Agent - Mac (A)"
  url: "http://<tailscale-ip-a>:18800"
```

Machine B (Linux, `<MACHINE_B_TAILSCALE_IP>`):
```yaml
agent:
  name: "Hermes Agent - Linux (B)"
  url: "http://<MACHINE_B_TAILSCALE_IP>:18800"
```

### Task 2.2: Write deployment script

Script to install and start gateway on remote machine:
```bash
scripts/deploy.sh <tailscale-ip> <config-path>
```

### Task 2.3: Write cross-machine test script

```bash
# From Machine A, send message to Machine B via A2A
curl -X POST http://<MACHINE_B_TAILSCALE_IP>:18800/a2a/jsonrpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "cross-001",
        "role": "user",
        "parts": [{"type": "text", "text": "Hello from Machine A!"}]
      }
    },
    "id": 1
  }'
```

### Task 2.4: Write peer discovery client

Simple A2A client that discovers remote agent card and sends messages:
```python
# scripts/a2a_client.py
import httpx
import json
import sys

async def discover_and_send(agent_url: str, message: str):
    async with httpx.AsyncClient() as client:
        # Discover
        card = await client.get(f"{agent_url}/.well-known/agent-card.json")
        print(f"Discovered: {card.json()['name']}")
        
        # Send
        resp = await client.post(f"{agent_url}/a2a/jsonrpc", json={
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": "cli-001",
                    "role": "user",
                    "parts": [{"type": "text", "text": message}]
                }
            },
            "id": 1
        })
        result = resp.json()
        print(f"Response: {json.dumps(result, indent=2)}")
```

---

## Acceptance Test Checklist

- [ ] Machine A gateway starts and health check passes
- [ ] Machine B gateway starts and health check passes
- [ ] Machine A can discover Machine B's Agent Card
- [ ] Machine A sends message to Machine B → receives response
- [ ] Machine B sends message to Machine A → receives response
- [ ] Multi-turn conversation works (contextId preserved)
- [ ] Both sides log the interaction in TaskStore

---

## 附录：网络配置指南

两台机器通过 **Tailscale VPN** 私有网络互联。部署前需获取并配置双方的 Tailscale IP。

### 获取 Tailscale IP

```bash
tailscale ip -4
# 在每台机器上运行，记录输出（100.x.x.x 格式）
```

### 变量说明

| 变量名 | 含义 | 示例 |
|--------|------|------|
| `<MACHINE_A_TAILSCALE_IP>` | Machine A（macOS，运行 Hermes）的 IP | `100.x.x.x` |
| `<MACHINE_B_TAILSCALE_IP>` | Machine B（Linux，远程 A2A Gateway）的 IP | `100.x.x.x` |

### 验证互通

```bash
# Machine A → Machine B
ping -c 3 <MACHINE_B_TAILSCALE_IP>
```

### 配置填写

将获取的 IP 替换 config.yaml 和文档中所有 `<MACHINE_x_TAILSCALE_IP>` 占位符：

```yaml
# Machine B 的 config.yaml
hermes:
  api_url: "http://<MACHINE_A_TAILSCALE_IP>:8642"
  api_key: "从 Machine A 的 ~/.hermes/.env 中获取 API_SERVER_KEY 的值"

agent:
  url: "http://<MACHINE_B_TAILSCALE_IP>:18800"
```

> 详细步骤见 `docs/specs/2026-05-24-a2a-gateway-design.md` 的「附录：网络配置指南」章节。
