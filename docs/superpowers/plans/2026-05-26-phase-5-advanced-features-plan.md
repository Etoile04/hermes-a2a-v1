# Phase 5: Advanced Features Implementation Plan

**Date:** 2026-05-26
**Branch:** `feat/phase-5-advanced-features`
**Base:** main @ `952d851`

---

## Task Overview (Execution Order)

| # | Task | Priority | Files | Tests |
|---|------|----------|-------|-------|
| 5.1 | Docker + CI/CD | 🔴 P0 | Dockerfile, compose.yaml, .github/workflows/ | CI |
| 5.2 | Streaming Enhancement | 🟡 P1 | a2a_handler.py | test_streaming.py |
| 5.3 | Push Notifications | 🟡 P1 | push_notifier.py, a2a_handler.py | test_push_notifications.py |
| 5.4 | Security Hardening | 🔴 P0 | rate_limiter.py, server.py | test_rate_limiter.py |
| 5.5 | Admin API | 🟡 P1 | admin_api.py, server.py | test_admin_api.py |
| 5.6 | A2A Client Enhancement | 🟢 P2 | a2a_client.py | test_a2a_client.py |
| 5.7 | Redis TaskStore (optional) | 🟢 P2 | redis_stores.py | test_redis_stores.py |

---

## Task 5.1: Docker + CI/CD

### 5.1.1 Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY src/ src/
EXPOSE 18800
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:18800/health
CMD ["python", "-m", "hermes_a2a.server"]
```

### 5.1.2 docker-compose.yaml
- gateway service with config mount
- optional redis service
- network for inter-container comms

### 5.1.3 GitHub Actions CI
- Trigger: push/PR to main
- Jobs: lint (ruff) → test (pytest) → build (docker) → push (GHCR on tag)

---

## Task 5.2: Streaming Enhancement

Current state: `on_message_send_stream` yields a single event.
Target: Stream Hermes SSE chunks as A2A events in real-time.

### Changes:
1. `a2a_handler.py`: `on_message_send_stream` — async generator yielding `TaskStatusUpdateEvent` per chunk
2. Wire `hermes_client.send_message_stream()` → yield intermediate WORKING events
3. Heartbeat: yield keepalive events every 15s during long waits
4. Update AgentCard `streaming: true` (already set)

---

## Task 5.3: Push Notifications

SDK provides REST endpoints for push notification configs:
- `POST /a2a/tasks/{id}/pushNotificationConfigs`
- `GET /a2a/tasks/{id}/pushNotificationConfigs`
- `DELETE /a2a/tasks/{id}/pushNotificationConfigs/{push_id}`

Handler stubs exist but return None/empty.

### Changes:
1. New `push_notifier.py`: PushNotificationManager
   - Store configs in SQLite
   - POST webhook on task completion/failure
   - Retry logic (3 attempts, exponential backoff)
2. `a2a_handler.py`: Implement on_create/get/delete/list push notification handlers
3. Update AgentCard `pushNotifications: true`

---

## Task 5.4: Security Hardening

### 5.4.1 Rate Limiting
- New `rate_limiter.py`: Token bucket per IP
- Configurable: requests/minute, burst size
- Applied as middleware

### 5.4.2 CORS Configuration
- Move from hardcoded `allow_origins=["*"]` to config
- Support environment-specific origins

### 5.4.3 TLS
- Document TLS termination with reverse proxy (nginx/caddy)
- Add `--ssl-keyfile` / `--ssl-certfile` CLI options

---

## Task 5.5: Admin API

### Endpoints:
- `GET /admin/peers` — list with health status
- `POST /admin/peers` — add peer dynamically
- `DELETE /admin/peers/{name}` — remove peer
- `POST /admin/peers/{name}/check` — health check
- `GET /admin/tasks` — list with filters
- `DELETE /admin/tasks/{id}` — delete task
- `POST /admin/tasks/{id}/retry` — retry failed task
- `GET /admin/metrics` — detailed metrics

### Auth:
- Admin API uses same Bearer token
- Separate admin_token config option

---

## Task 5.6: A2A Client Enhancement

Current: Basic HermesA2AClient wrapper.
Target:
- Connection pooling (httpx.AsyncClient reuse)
- Auto peer discovery on startup
- Health monitoring with periodic checks
- Circuit breaker for unreachable peers

---

## Task 5.7: Redis TaskStore (Optional)

Only if Redis is deployed (docker-compose).
- Implement `RedisTaskStore` and `RedisSessionStore`
- Config: `task_store.type: "redis"` with `url` field
- Fallback to SQLite if Redis unavailable

---

## Execution Strategy

Tasks 5.1 (Docker) and 5.4 (Security) are independent P0 items → parallel.
Tasks 5.2, 5.3, 5.5 depend on handler internals → sequential.
Tasks 5.6, 5.7 are P2 → after P0/P1 done.
