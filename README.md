# Hermes A2A v1.0 Gateway

An **A2A (Agent-to-Agent) protocol gateway** that bridges the [A2A v1.0 specification](https://github.com/a2aproject/a2a-spec) to the [Hermes Agent](https://github.com/nousresearch/hermes) API — making any Hermes instance discoverable and callable as a first-class A2A agent.

## Architecture

```
┌──────────────┐   A2A v1.0    ┌───────────────────────┐   HTTP/REST   ┌──────────────┐
│  A2A Client  │ ◄──────────► │  Hermes A2A Gateway   │ ◄───────────► │ Hermes Agent │
│  (any A2A-   │   JSON-RPC   │  (FastAPI + a2a-sdk)  │  /v1/chat/    │   API        │
│   aware app) │   + AgentCard│  ├─ HermesA2AHandler  │  completions  │              │
└──────────────┘              │  ├─ SQLiteTaskStore   │               └──────────────┘
                              │  └─ SessionStore      │
                              └───────────────────────┘
```

**Key components:**

| Component | Description |
|---|---|
| `server.py` | FastAPI app factory — routes, auth middleware, health/metrics endpoints |
| `a2a_handler.py` | Bridges A2A `RequestHandler` callbacks → Hermes API calls (protobuf-based) |
| `hermes_client.py` | Async HTTP client for Hermes API with retry, timeout, and streaming support |
| `task_store.py` | SQLite-backed A2A task persistence |
| `session_store.py` | SQLite-backed `contextId → sessionId` mapping for multi-turn conversations |
| `config.py` / `models.py` | YAML config loader + Pydantic v2 validation models |

## Quick Start

### Install

```bash
cd hermes-a2a-v1
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Configure

Create `~/.hermes/a2a-gateway/config.yaml` (or any path):

```yaml
server:
  host: "0.0.0.0"
  port: 18800

hermes:
  api_url: "http://localhost:8642"   # Your Hermes instance
  timeout: 300
  api_key: ""                        # Optional API key

agent:
  name: "Hermes Agent"
  description: "AI Agent powered by Hermes via A2A v1.0"
  url: "http://localhost:18800"
  skills:
    - id: "general"
      name: "General Q&A"
      description: "Answer general questions"
    - id: "coding"
      name: "Code Review"
      description: "Review and write source code"

auth:
  enabled: true
  token: "your-secret-bearer-token"

task_store:
  type: sqlite
  path: "~/.hermes/a2a-gateway/tasks.db"

logging:
  level: INFO
```

> All fields have sensible defaults — you only need to override what differs.

### Run

```bash
# Option 1: via entry point
hermes-a2a

# Option 2: as a Python module
python -m hermes_a2a

# Option 3: with explicit config
HERMES_A2A_CONFIG=/path/to/config.yaml python -m hermes_a2a
```

The gateway starts on `http://0.0.0.0:18800` by default.

### Verify

```bash
# Agent card (A2A discovery)
curl http://localhost:18800/.well-known/agent-card.json

# Health check
curl http://localhost:18800/health

# Metrics
curl http://localhost:18800/metrics
```

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_server.py -v

# With coverage (if pytest-cov installed)
python -m pytest tests/ -v --cov=hermes_a2a
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/.well-known/agent-card.json` | GET | A2A Agent Card (discovery) |
| `/` | POST | A2A JSON-RPC endpoint (v1.0 + v0.3 compat) |
| `/health` | GET | Gateway health + Hermes reachability |
| `/metrics` | GET | Request counters, active tasks/sessions |

### Supported A2A Methods

**v1.0 (CamelCase):**
- `SendMessage` — send a message and get a task
- `SendStreamingMessage` — streaming response via SSE
- `GetTask` — retrieve task by ID
- `CancelTask` — cancel a running task
- `ListTasks` — list all tasks

**v0.3 compat (slash-case):**
- `message/send`, `tasks/get`, `tasks/cancel` — automatic protocol adaptation

## Deployment

### Docker (example)

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
EXPOSE 18800
CMD ["hermes-a2a"]
```

```bash
docker build -t hermes-a2a .
docker run -p 18800:18800 \
  -v ~/.hermes/a2a-gateway:/root/.hermes/a2a-gateway \
  hermes-a2a
```

### systemd (example)

```ini
[Unit]
Description=Hermes A2A Gateway
After=network.target

[Service]
Type=simple
User=hermes
Environment=HERMES_A2A_CONFIG=/etc/hermes-a2a/config.yaml
ExecStart=/opt/hermes-a2a/.venv/bin/hermes-a2a
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## License

See project root for license information.
