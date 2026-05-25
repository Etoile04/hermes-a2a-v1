# Phase 4: OpenClaw A2A Interop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable full production-grade A2A v1.0/v0.3 interoperability with OpenClaw a2a-gateway

**Architecture:** Extend hermes-a2a-v1 with REST transport, enriched AgentCard, task state machine validation, multi-part message support, and bidirectional A2A client capabilities using a2a-sdk's built-in Client infrastructure.

**Tech Stack:** Python a2a-sdk 1.0.3, FastAPI, SQLite, httpx, asyncio

---

## File Structure

**New Files:**
- `src/hermes_a2a/task_state_machine.py` - Task state transition validation
- `src/hermes_a2a/message_parser.py` - Multi-part message handling  
- `src/hermes_a2a/a2a_client.py` - A2A client wrapper for peer communication
- `src/hermes_a2a/peer_manager.py` - Peer discovery and routing
- `tests/test_rest_transport.py` - REST endpoint tests
- `tests/test_agent_card.py` - Enhanced AgentCard tests
- `tests/test_task_state_machine.py` - State machine validation tests
- `tests/test_message_parser.py` - Multi-part message tests
- `tests/test_a2a_client.py` - A2A client tests
- `tests/test_peer_manager.py` - Peer integration tests

**Modified Files:**
- `src/hermes_a2a/server.py` - Add REST routes, enrich AgentCard
- `src/hermes_a2a/a2a_handler.py` - Integrate state machine + message parser
- `src/hermes_a2a/models.py` - Add peer configuration models
- `tests/conftest.py` - Add A2A peer fixtures

---

### Task 1: REST Transport Exposure

**Files:**
- Modify: `src/hermes_a2a/server.py:183-193`
- Create: `tests/test_rest_transport.py`

- [ ] **Step 1: Add REST routes to server.py**

```python
# In server.py, after line 192 (after rpc_routes creation)
from a2a.server.routes import create_rest_routes

# REST endpoint at /a2a/* (A2A spec standard)
rest_routes = create_rest_routes(
    request_handler=handler,
    path_prefix="/a2a",
    enable_v0_3_compat=True,
)
```

- [ ] **Step 2: Mount REST routes in FastAPI app**

```python
# In server.py, after line 251 (after app.routes.extend(rpc_routes))
app.routes.extend(rest_routes)
```

- [ ] **Step 3: Write failing test for REST endpoints**

```python
# tests/test_rest_transport.py
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(mock_hermes_client, task_store, session_store):
    from hermes_a2a.server import create_app
    app = create_app()
    app.state.gateway = {
        "hermes_client": mock_hermes_client,
        "task_store": task_store, 
        "session_store": session_store,
    }
    return TestClient(app)

def test_rest_message_send(client):
    response = client.post("/a2a/message:send", json={
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Hello REST"}],
            "messageId": "rest-001"
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["id"] 
    assert data["status"]["state"] == 3  # COMPLETED
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_rest_transport.py::test_rest_message_send -v`
Expected: FAIL with ImportError or route not found

- [ ] **Step 5: Implement REST route mounting**

Add the REST route code from steps 1-2 to server.py

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_rest_transport.py::test_rest_message_send -v`
Expected: PASS

- [ ] **Step 7: Add v0.3 REST compatibility test**

```python
def test_rest_v03_tasks_get(client):
    # First create a task via JSON-RPC
    rpc_response = client.post("/", json={
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "test"}]}},
        "id": 1
    })
    task_id = rpc_response.json()["result"]["id"]
    
    # Then fetch via REST v0.3
    response = client.get(f"/a2a/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == task_id
```

- [ ] **Step 8: Run v0.3 test to verify compatibility**

Run: `pytest tests/test_rest_transport.py::test_rest_v03_tasks_get -v`
Expected: PASS (v0.3 compat should work automatically)

- [ ] **Step 9: Commit**

```bash
git add src/hermes_a2a/server.py tests/test_rest_transport.py
git commit -m "feat: expose REST transport with v0.3 compatibility

- Add create_rest_routes() with path_prefix=/a2a
- Enable v0.3 compat for REST endpoints  
- Add tests for REST message:send and tasks/{id}
- Both JSON-RPC and REST transports now available"
```

### Task 2: AgentCard Enhancement

**Files:**
- Modify: `src/hermes_a2a/server.py:46-68`
- Modify: `src/hermes_a2a/models.py:31-38`
- Create: `tests/test_agent_card.py`

- [ ] **Step 1: Write failing test for enhanced AgentCard**

```python
# tests/test_agent_card.py
import pytest
from fastapi.testclient import TestClient

def test_agent_card_fields(client):
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    
    # Enhanced fields
    assert "provider" in card
    assert card["provider"]["organization"] == "Hermes"
    assert card["provider"]["url"]
    
    assert "documentationUrl" in card  
    assert "securitySchemes" in card
    
    schemes = card["securitySchemes"]
    assert "bearer" in schemes
    assert schemes["bearer"]["type"] == "http"
    assert schemes["bearer"]["scheme"] == "bearer"
    
    assert "securityRequirements" in card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_card.py::test_agent_card_fields -v`
Expected: FAIL with missing fields

- [ ] **Step 3: Add provider config to models.py**

```python
# In models.py, add after AgentConfig class
class AgentProviderConfig(BaseModel):
    """Agent provider information."""
    organization: str = "Hermes"  
    url: str = "https://github.com/Etoile04/hermes-a2a-v1"

# Modify AgentConfig to include provider
class AgentConfig(BaseModel):
    """Agent identity and capabilities."""
    name: str = "Hermes Agent"
    description: str = "AI Agent powered by Hermes via A2A v1.0"
    url: str = "http://localhost:18800"
    documentation_url: str = "https://github.com/Etoile04/hermes-a2a-v1/blob/main/README.md"
    provider: AgentProviderConfig = Field(default_factory=AgentProviderConfig)
    skills: list[AgentSkillConfig] = Field(default_factory=list)
```

- [ ] **Step 4: Run config test to verify model changes work**

Run: `python -c "from hermes_a2a.models import GatewayConfig; print(GatewayConfig())"`
Expected: No errors, shows default config with provider

- [ ] **Step 5: Update _build_agent_card in server.py**

```python
def _build_agent_card(cfg: Any) -> AgentCard:
    """Create an a2a-sdk AgentCard protobuf from our GatewayConfig."""
    from a2a.types.a2a_pb2 import (
        AgentProvider, SecurityScheme, SecurityRequirement,
        HTTPAuthSecurityScheme
    )
    
    skills = []
    for s in cfg.agent.skills:
        skills.append(AgentSkill(id=s.id, name=s.name, description=s.description))
    if not skills:
        skills.append(
            AgentSkill(id="general", name="General Q&A", description="General conversation")
        )

    # Build provider
    provider = AgentProvider(
        organization=cfg.agent.provider.organization,
        url=cfg.agent.provider.url,
    )
    
    # Build security schemes
    bearer_auth = HTTPAuthSecurityScheme(
        scheme="bearer",
        description="Bearer token authentication",
    )
    
    security_scheme = SecurityScheme()
    security_scheme.http_auth_security_scheme.CopyFrom(bearer_auth)
    
    security_schemes = {"bearer": security_scheme}
    
    # Build security requirements  
    security_requirement = SecurityRequirement()
    security_requirement.schemes["bearer"] = b""  # Empty bytes for scheme reference
    
    return AgentCard(
        name=cfg.agent.name,
        description=cfg.agent.description,
        provider=provider,
        version=VERSION,
        documentation_url=cfg.agent.documentation_url,
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
        ),
        security_schemes=security_schemes,
        security_requirements=[security_requirement],
        skills=skills,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )
```

- [ ] **Step 6: Run enhanced AgentCard test**

Run: `pytest tests/test_agent_card.py::test_agent_card_fields -v`
Expected: PASS

- [ ] **Step 7: Test AgentCard with real server startup**

```bash
cd /Users/lwj04/projects/hermes-a2a-v1
echo 'server: {host: "0.0.0.0", port: 18801}' > test-card.yaml
HERMES_A2A_CONFIG=test-card.yaml python -m hermes_a2a &
sleep 2
curl -s http://localhost:18801/.well-known/agent-card.json | python -m json.tool
kill %1
```

Expected: See enhanced fields in AgentCard JSON

- [ ] **Step 8: Commit**  

```bash
git add src/hermes_a2a/server.py src/hermes_a2a/models.py tests/test_agent_card.py
git commit -m "feat: enrich AgentCard with provider and security schemes

- Add AgentProviderConfig to models
- Add documentation_url to agent config  
- Build provider, securitySchemes, securityRequirements in AgentCard
- Add tests for enhanced AgentCard fields
- AgentCard now OpenClaw-compatible with full metadata"
```

### Task 3: Task State Machine Validation

**Files:**
- Create: `src/hermes_a2a/task_state_machine.py`
- Modify: `src/hermes_a2a/a2a_handler.py:280-294` 
- Create: `tests/test_task_state_machine.py`

- [ ] **Step 1: Write failing test for state transitions**

```python
# tests/test_task_state_machine.py
import pytest
from hermes_a2a.task_state_machine import TaskStateMachine, InvalidStateTransitionError
from a2a.types.a2a_pb2 import TaskState

def test_valid_state_transitions():
    sm = TaskStateMachine()
    
    # Valid: SUBMITTED -> WORKING
    sm.validate_transition(TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)
    
    # Valid: WORKING -> COMPLETED  
    sm.validate_transition(TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED)
    
    # Valid: WORKING -> FAILED
    sm.validate_transition(TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_FAILED)

def test_invalid_state_transitions():
    sm = TaskStateMachine()
    
    # Invalid: COMPLETED -> WORKING (terminal state)
    with pytest.raises(InvalidStateTransitionError):
        sm.validate_transition(TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_WORKING)
        
    # Invalid: FAILED -> WORKING (terminal state)  
    with pytest.raises(InvalidStateTransitionError):
        sm.validate_transition(TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_WORKING)

def test_cancel_task_valid_states():
    sm = TaskStateMachine()
    
    # Can cancel SUBMITTED or WORKING
    assert sm.can_cancel(TaskState.TASK_STATE_SUBMITTED) == True
    assert sm.can_cancel(TaskState.TASK_STATE_WORKING) == True
    
    # Cannot cancel terminal states
    assert sm.can_cancel(TaskState.TASK_STATE_COMPLETED) == False
    assert sm.can_cancel(TaskState.TASK_STATE_FAILED) == False
    assert sm.can_cancel(TaskState.TASK_STATE_CANCELED) == False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_state_machine.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Create task state machine implementation**

```python
# src/hermes_a2a/task_state_machine.py
"""Task state machine validation for A2A protocol compliance."""

from a2a.types.a2a_pb2 import TaskState


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    
    def __init__(self, from_state: int, to_state: int):
        self.from_state = from_state
        self.to_state = to_state
        from_name = TaskState.Name(from_state)
        to_name = TaskState.Name(to_state)
        super().__init__(f"Invalid transition: {from_name} -> {to_name}")


class TaskStateMachine:
    """Validates A2A task state transitions according to protocol spec."""
    
    # Define valid state transitions
    _VALID_TRANSITIONS = {
        TaskState.TASK_STATE_SUBMITTED: {
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_FAILED, 
            TaskState.TASK_STATE_CANCELED,
        },
        TaskState.TASK_STATE_WORKING: {
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_INPUT_REQUIRED,
        },
        TaskState.TASK_STATE_INPUT_REQUIRED: {
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_CANCELED,
        },
        # Terminal states have no valid transitions
        TaskState.TASK_STATE_COMPLETED: set(),
        TaskState.TASK_STATE_FAILED: set(), 
        TaskState.TASK_STATE_CANCELED: set(),
    }
    
    _TERMINAL_STATES = {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    }
    
    _CANCELABLE_STATES = {
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING, 
        TaskState.TASK_STATE_INPUT_REQUIRED,
    }
    
    def validate_transition(self, from_state: int, to_state: int) -> None:
        """Validate a state transition. Raises InvalidStateTransitionError if invalid."""
        valid_next_states = self._VALID_TRANSITIONS.get(from_state, set())
        if to_state not in valid_next_states:
            raise InvalidStateTransitionError(from_state, to_state)
    
    def can_cancel(self, current_state: int) -> bool:
        """Check if a task in the given state can be canceled."""
        return current_state in self._CANCELABLE_STATES
    
    def is_terminal(self, state: int) -> bool:
        """Check if a state is terminal (no further transitions allowed)."""
        return state in self._TERMINAL_STATES
```

- [ ] **Step 4: Run state machine tests**

Run: `pytest tests/test_task_state_machine.py -v`
Expected: PASS

- [ ] **Step 5: Write failing integration test for cancel validation** 

```python
# Add to tests/test_task_state_machine.py
@pytest.mark.asyncio
async def test_cancel_task_state_validation(task_store):
    """Test that cancel_task validates current state before canceling."""
    from hermes_a2a.a2a_handler import HermesA2AHandler
    from hermes_a2a.hermes_client import HermesClient
    from unittest.mock import AsyncMock
    
    client = AsyncMock(spec=HermesClient)
    handler = HermesA2AHandler(client, task_store)
    
    # Create a completed task
    completed_task = {
        "id": "task-completed",
        "contextId": "ctx-1", 
        "status": {"state": "completed"},
        "response": "Done"
    }
    await task_store.save(completed_task)
    
    # Try to cancel completed task - should fail
    from a2a.types.a2a_pb2 import CancelTaskRequest
    from a2a.server.context import ServerCallContext
    
    req = CancelTaskRequest(id="task-completed")
    ctx = ServerCallContext(tenant_id="test", owner_id="test")
    
    result = await handler.on_cancel_task(req, ctx)
    assert result is None  # Should return None when cancel fails due to state
```

- [ ] **Step 6: Run integration test to verify it fails**

Run: `pytest tests/test_task_state_machine.py::test_cancel_task_state_validation -v`
Expected: FAIL (cancel should succeed but we want it to validate state)

- [ ] **Step 7: Integrate state machine into a2a_handler.py**

```python
# In a2a_handler.py, add import at top
from hermes_a2a.task_state_machine import TaskStateMachine, InvalidStateTransitionError

# In HermesA2AHandler.__init__, add:
def __init__(
    self,
    hermes_client: HermesClient,
    task_store: SQLiteTaskStore,
    session_store: SessionStore | None = None,
) -> None:
    self._hermes = hermes_client
    self._store = task_store
    self._session_store = session_store
    self._sessions: dict[str, str] = {}
    self._state_machine = TaskStateMachine()  # Add this line

# Modify on_cancel_task method:
async def on_cancel_task(
    self,
    params: CancelTaskRequest,
    context: ServerCallContext,
) -> Task | None:
    task_dict = await self._store.get(params.id, context)
    if task_dict is None:
        return None
    
    # Get current state
    current_state_str = task_dict.get("status", {}).get("state", "")
    state_map = {
        "submitted": TaskState.TASK_STATE_SUBMITTED,
        "working": TaskState.TASK_STATE_WORKING, 
        "completed": TaskState.TASK_STATE_COMPLETED,
        "canceled": TaskState.TASK_STATE_CANCELED,
        "failed": TaskState.TASK_STATE_FAILED,
    }
    current_state = state_map.get(current_state_str, TaskState.TASK_STATE_SUBMITTED)
    
    # Validate cancellation is allowed
    if not self._state_machine.can_cancel(current_state):
        logger.warning(
            "Cannot cancel task %s in state %s", 
            params.id, TaskState.Name(current_state)
        )
        return None
    
    # Proceed with cancellation
    task_dict["status"]["state"] = "canceled"
    await self._store.save(task_dict, context)
    return _make_task(
        task_dict["id"],
        task_dict.get("contextId", ""),
        TaskState.TASK_STATE_CANCELED,
    )
```

- [ ] **Step 8: Run integration test to verify state validation works**

Run: `pytest tests/test_task_state_machine.py::test_cancel_task_state_validation -v`  
Expected: PASS (cancel should now be blocked for completed tasks)

- [ ] **Step 9: Commit**

```bash
git add src/hermes_a2a/task_state_machine.py src/hermes_a2a/a2a_handler.py tests/test_task_state_machine.py
git commit -m "feat: add task state machine validation

- Create TaskStateMachine with A2A protocol transition rules
- Add InvalidStateTransitionError exception
- Integrate state validation into cancel_task handler
- Add comprehensive tests for valid/invalid transitions
- Terminal states (COMPLETED/FAILED/CANCELED) cannot be modified"
```

### Task 4: Multi-part Message Support

**Files:**
- Create: `src/hermes_a2a/message_parser.py`
- Modify: `src/hermes_a2a/a2a_handler.py:107-110,132-179`
- Create: `tests/test_message_parser.py`

- [ ] **Step 1: Write failing test for multi-part message parsing**

```python
# tests/test_message_parser.py
import pytest
from hermes_a2a.message_parser import MessageParser
from a2a.types.a2a_pb2 import Part, SendMessageRequest, Message

def test_text_only_message():
    parser = MessageParser()
    
    # Create message with single text part
    part = Part(text="Hello world")
    message = Message(role="ROLE_USER", parts=[part])
    request = SendMessageRequest(message=message)
    
    result = parser.extract_text_for_hermes(request)
    assert result == "Hello world"

def test_multi_part_text_message():
    parser = MessageParser()
    
    # Multiple text parts should be concatenated
    parts = [
        Part(text="Hello "), 
        Part(text="world"),
        Part(text="!")
    ]
    message = Message(role="ROLE_USER", parts=parts)
    request = SendMessageRequest(message=message)
    
    result = parser.extract_text_for_hermes(request)
    assert result == "Hello world!"

def test_url_part_description():
    parser = MessageParser()
    
    parts = [
        Part(text="Check this file: "),
        Part(url="https://example.com/document.pdf", filename="document.pdf", media_type="application/pdf"),
    ]
    message = Message(role="ROLE_USER", parts=parts)
    request = SendMessageRequest(message=message)
    
    result = parser.extract_text_for_hermes(request)
    expected = "Check this file: [FILE: document.pdf (application/pdf) - https://example.com/document.pdf]"
    assert result == expected

def test_data_part_description():
    parser = MessageParser()
    
    parts = [
        Part(text="Analyze this data: "), 
        Part(data=b'{"key": "value"}', media_type="application/json"),
    ]
    message = Message(role="ROLE_USER", parts=parts)
    request = SendMessageRequest(message=message)
    
    result = parser.extract_text_for_hermes(request)
    expected = 'Analyze this data: [DATA: application/json - {"key": "value"}]'
    assert result == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_parser.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Create message parser implementation**

```python
# src/hermes_a2a/message_parser.py
"""Multi-part message parsing for A2A protocol."""

import logging
from a2a.types.a2a_pb2 import SendMessageRequest, Part

logger = logging.getLogger(__name__)


class MessageParser:
    """Parses A2A multi-part messages and converts them to Hermes-compatible text."""
    
    def extract_text_for_hermes(self, params: SendMessageRequest) -> str:
        """Extract all parts from SendMessageRequest and convert to text for Hermes API.
        
        Text parts: Used directly
        URL parts: Converted to [FILE: filename (media_type) - url] description
        Data parts: Converted to [DATA: media_type - content] description
        """
        parts = list(params.message.parts)
        text_segments = []
        
        for part in parts:
            if part.text:
                text_segments.append(part.text)
            elif part.url:
                filename = part.filename or "unknown"
                media_type = part.media_type or "unknown"
                description = f"[FILE: {filename} ({media_type}) - {part.url}]"
                text_segments.append(description)
            elif part.data:
                media_type = part.media_type or "binary"
                # Decode data if it's text-like
                try:
                    data_str = part.data.decode('utf-8')
                except UnicodeDecodeError:
                    data_str = f"<{len(part.data)} bytes>"
                description = f"[DATA: {media_type} - {data_str}]"
                text_segments.append(description)
            elif part.raw:
                # Raw bytes - describe size
                description = f"[RAW: {len(part.raw)} bytes]"
                text_segments.append(description)
        
        return "".join(text_segments)
    
    def create_text_response_parts(self, response_text: str) -> list[Part]:
        """Convert Hermes response text back to A2A Part format."""
        return [Part(text=response_text)]
```

- [ ] **Step 4: Run message parser tests**

Run: `pytest tests/test_message_parser.py -v`
Expected: PASS

- [ ] **Step 5: Write integration test for multi-part in handler**

```python
# Add to tests/test_message_parser.py
@pytest.mark.asyncio
async def test_handler_multi_part_integration(mock_hermes_client, task_store, session_store):
    """Test that a2a_handler uses MessageParser for multi-part messages."""
    from hermes_a2a.a2a_handler import HermesA2AHandler
    from a2a.types.a2a_pb2 import SendMessageRequest, Message, Part
    from a2a.server.context import ServerCallContext
    
    handler = HermesA2AHandler(mock_hermes_client, task_store, session_store)
    
    # Create multi-part message
    parts = [
        Part(text="Please analyze "),
        Part(url="https://example.com/data.json", filename="data.json", media_type="application/json"),
    ]
    message = Message(role="ROLE_USER", parts=parts, message_id="multi-001")
    request = SendMessageRequest(message=message)
    context = ServerCallContext(tenant_id="test", owner_id="test")
    
    # Handler should parse multi-part and send combined text to Hermes
    task = await handler.on_message_send(request, context)
    
    # Verify Hermes client was called with parsed text
    mock_hermes_client.send_message.assert_called_once()
    sent_text = mock_hermes_client.send_message.call_args[0][0]
    assert "Please analyze" in sent_text
    assert "[FILE: data.json (application/json) - https://example.com/data.json]" in sent_text
```

- [ ] **Step 6: Run integration test to verify it fails**

Run: `pytest tests/test_message_parser.py::test_handler_multi_part_integration -v`
Expected: FAIL (handler doesn't use MessageParser yet)

- [ ] **Step 7: Integrate MessageParser into a2a_handler.py**

```python
# In a2a_handler.py, add import at top
from hermes_a2a.message_parser import MessageParser

# In HermesA2AHandler.__init__, add:
def __init__(
    self,
    hermes_client: HermesClient,
    task_store: SQLiteTaskStore,
    session_store: SessionStore | None = None,
) -> None:
    self._hermes = hermes_client
    self._store = task_store
    self._session_store = session_store
    self._sessions: dict[str, str] = {}
    self._state_machine = TaskStateMachine()
    self._message_parser = MessageParser()  # Add this line

# Replace _extract_text method:
def _extract_text(self, params: SendMessageRequest) -> str:
    """Extract text from multi-part message using MessageParser."""
    return self._message_parser.extract_text_for_hermes(params)
```

- [ ] **Step 8: Run integration test to verify MessageParser integration**

Run: `pytest tests/test_message_parser.py::test_handler_multi_part_integration -v`
Expected: PASS

- [ ] **Step 9: Add streaming support test**

```python
# Add to tests/test_message_parser.py
@pytest.mark.asyncio
async def test_streaming_multi_part_support(mock_hermes_client, task_store, session_store):
    """Test multi-part messages work with streaming responses."""
    from hermes_a2a.a2a_handler import HermesA2AHandler
    from a2a.types.a2a_pb2 import SendMessageRequest, Message, Part
    from a2a.server.context import ServerCallContext
    
    # Mock streaming response
    def mock_stream():
        async def _stream():
            yield "Chunk 1"
            yield "Chunk 2"
        return _stream()
    
    mock_hermes_client.send_message_stream = mock_stream
    
    handler = HermesA2AHandler(mock_hermes_client, task_store, session_store)
    
    # Multi-part streaming request
    parts = [Part(text="Stream this: "), Part(data=b"test", media_type="text/plain")]
    message = Message(role="ROLE_USER", parts=parts)
    request = SendMessageRequest(message=message)  
    context = ServerCallContext(tenant_id="test", owner_id="test")
    
    # Collect streaming events
    events = []
    async for event in handler.on_message_send_stream(request, context):
        events.append(event)
    
    assert len(events) >= 3  # Working, chunk events, completed
```

- [ ] **Step 10: Run streaming test**

Run: `pytest tests/test_message_parser.py::test_streaming_multi_part_support -v`
Expected: PASS (streaming should work with multi-part)

- [ ] **Step 11: Commit**

```bash
git add src/hermes_a2a/message_parser.py src/hermes_a2a/a2a_handler.py tests/test_message_parser.py
git commit -m "feat: add multi-part message support

- Create MessageParser for A2A Part handling
- Support text, url, data, and raw Part types  
- Convert non-text parts to descriptive text for Hermes
- Integrate MessageParser into both sync and streaming handlers
- Add comprehensive tests for multi-part message scenarios"
```

### Task 5: A2A Client and Peer Management

**Files:**
- Create: `src/hermes_a2a/a2a_client.py`
- Create: `src/hermes_a2a/peer_manager.py`
- Modify: `src/hermes_a2a/models.py:54-63` (add PeerConfig)
- Modify: `src/hermes_a2a/server.py:92-252` (add peer routes)
- Create: `tests/test_a2a_client.py`
- Create: `tests/test_peer_manager.py`

- [ ] **Step 1: Write failing test for A2A client wrapper**

```python
# tests/test_a2a_client.py
import pytest
from unittest.mock import AsyncMock, Mock
import httpx

@pytest.mark.asyncio
async def test_a2a_client_discover_agent():
    """Test A2A client can discover remote agent card."""
    from hermes_a2a.a2a_client import HermesA2AClient
    
    client = HermesA2AClient()
    
    # Mock successful agent card discovery
    mock_card = Mock()
    mock_card.name = "Remote Agent"
    mock_card.version = "1.0"
    
    card = await client.discover_agent("http://remote:18800/.well-known/agent-card.json")
    assert card is not None

@pytest.mark.asyncio  
async def test_a2a_client_send_message():
    """Test A2A client can send message to remote agent."""
    from hermes_a2a.a2a_client import HermesA2AClient
    
    client = HermesA2AClient()
    
    # Mock agent card and client
    task = await client.send_message(
        agent_url="http://remote:18800",
        message="Hello remote agent",
        context_id="test-ctx"
    )
    assert task is not None
    assert task.id

@pytest.mark.asyncio
async def test_a2a_client_connection_error():
    """Test A2A client handles connection errors gracefully."""
    from hermes_a2a.a2a_client import HermesA2AClient, A2AClientError
    
    client = HermesA2AClient()
    
    with pytest.raises(A2AClientError):
        await client.discover_agent("http://nonexistent:99999/.well-known/agent-card.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_a2a_client.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Create A2A client wrapper implementation**

```python
# src/hermes_a2a/a2a_client.py
"""A2A client wrapper for peer communication."""

import asyncio
import logging
from typing import Any

import httpx
from a2a.client import ClientFactory
from a2a.client.card_resolver import A2ACardResolver  
from a2a.types.a2a_pb2 import AgentCard, SendMessageRequest, Message, Part, Task

logger = logging.getLogger(__name__)


class A2AClientError(Exception):
    """Raised when A2A client operations fail."""


class HermesA2AClient:
    """Wrapper around a2a-sdk Client for peer communication."""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._httpx_client: httpx.AsyncClient | None = None
        self._client_factory = ClientFactory()
    
    async def _get_httpx_client(self) -> httpx.AsyncClient:
        """Get or create httpx client for agent card resolution."""
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(timeout=self.timeout)
        return self._httpx_client
    
    async def close(self) -> None:
        """Close the client and cleanup resources."""
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None
    
    async def discover_agent(self, agent_card_url: str) -> AgentCard:
        """Discover remote agent by fetching its agent card.
        
        Args:
            agent_card_url: Full URL to the agent's .well-known/agent-card.json
            
        Returns:
            AgentCard protobuf object
            
        Raises:
            A2AClientError: If discovery fails
        """
        try:
            httpx_client = await self._get_httpx_client()
            
            # Parse base URL from agent card URL
            base_url = agent_card_url.replace("/.well-known/agent-card.json", "")
            
            resolver = A2ACardResolver(httpx_client, base_url)
            card = await resolver.get_agent_card()
            
            logger.info("Discovered agent: %s (v%s) at %s", card.name, card.version, base_url)
            return card
            
        except Exception as exc:
            logger.error("Failed to discover agent at %s: %s", agent_card_url, exc)
            raise A2AClientError(f"Agent discovery failed: {exc}") from exc
    
    async def send_message(
        self, 
        agent_url: str, 
        message: str,
        context_id: str | None = None,
        auth_token: str | None = None
    ) -> Task:
        """Send a message to a remote A2A agent.
        
        Args:
            agent_url: Base URL of the remote agent
            message: Text message to send
            context_id: Optional conversation context ID
            auth_token: Optional bearer token for authentication
            
        Returns:
            Task protobuf object with the response
            
        Raises:
            A2AClientError: If message sending fails
        """
        try:
            # Discover agent first
            agent_card_url = f"{agent_url}/.well-known/agent-card.json"
            card = await self.discover_agent(agent_card_url)
            
            # Create SDK client for this agent
            client = self._client_factory.create(card)
            
            # Build message
            parts = [Part(text=message)]
            msg = Message(role="ROLE_USER", parts=parts)
            if context_id:
                msg.context_id = context_id
            
            request = SendMessageRequest(message=msg)
            
            # Send message  
            task = await client.send_message(request)
            
            logger.info("Sent message to %s, task_id=%s", agent_url, task.id)
            return task
            
        except Exception as exc:
            logger.error("Failed to send message to %s: %s", agent_url, exc)
            raise A2AClientError(f"Message sending failed: {exc}") from exc
        finally:
            # Always close the SDK client
            if 'client' in locals():
                await client.close()
```

- [ ] **Step 4: Run A2A client tests (should fail on mocking)**

Run: `pytest tests/test_a2a_client.py -v`
Expected: FAIL (tests need proper mocking of a2a-sdk components)

- [ ] **Step 5: Fix test mocking and run again**

```python
# Update tests/test_a2a_client.py with proper mocking
import pytest
from unittest.mock import AsyncMock, Mock, patch

@pytest.mark.asyncio
async def test_a2a_client_discover_agent():
    """Test A2A client can discover remote agent card."""
    from hermes_a2a.a2a_client import HermesA2AClient
    
    with patch('hermes_a2a.a2a_client.A2ACardResolver') as mock_resolver_class:
        mock_resolver = AsyncMock()
        mock_card = Mock()
        mock_card.name = "Remote Agent"
        mock_card.version = "1.0"
        mock_resolver.get_agent_card.return_value = mock_card
        mock_resolver_class.return_value = mock_resolver
        
        client = HermesA2AClient()
        card = await client.discover_agent("http://remote:18800/.well-known/agent-card.json")
        
        assert card.name == "Remote Agent"
        assert card.version == "1.0"
```

Run: `pytest tests/test_a2a_client.py::test_a2a_client_discover_agent -v`
Expected: PASS

- [ ] **Step 6: Write failing test for peer management**

```python
# tests/test_peer_manager.py
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_peer_manager_list_peers():
    """Test peer manager can list configured peers."""
    from hermes_a2a.peer_manager import PeerManager
    from hermes_a2a.models import PeerConfig
    
    peers_config = [
        PeerConfig(
            name="openclaw-agent",
            agent_card_url="http://localhost:18801/.well-known/agent-card.json",
            auth_token="secret123"
        )
    ]
    
    manager = PeerManager(peers_config)
    peers = await manager.list_peers()
    
    assert len(peers) == 1
    assert peers[0]["name"] == "openclaw-agent"
    assert peers[0]["status"] == "unknown"  # Not discovered yet

@pytest.mark.asyncio  
async def test_peer_manager_discover_all():
    """Test peer manager can discover all configured peers."""
    from hermes_a2a.peer_manager import PeerManager
    from hermes_a2a.models import PeerConfig
    
    peers_config = [
        PeerConfig(name="test", agent_card_url="http://test/.well-known/agent-card.json")
    ]
    
    manager = PeerManager(peers_config)
    
    # Mock the A2A client
    mock_client = AsyncMock()
    mock_card = AsyncMock()
    mock_card.name = "Test Agent"
    mock_client.discover_agent.return_value = mock_card
    manager._a2a_client = mock_client
    
    discovered = await manager.discover_all()
    assert len(discovered) == 1
    assert discovered[0]["name"] == "test"
    assert discovered[0]["status"] == "available"
```

- [ ] **Step 7: Run peer manager test to verify it fails**

Run: `pytest tests/test_peer_manager.py -v`  
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 8: Add peer configuration models**

```python
# Add to src/hermes_a2a/models.py
class PeerConfig(BaseModel):
    """Configuration for a remote A2A peer agent."""
    name: str
    agent_card_url: str
    auth_token: str = ""
    enabled: bool = True

# Modify GatewayConfig to include peers
class GatewayConfig(BaseModel):
    """Top-level gateway configuration."""
    server: ServerConfig = Field(default_factory=ServerConfig)
    hermes: HermesConfig = Field(default_factory=HermesConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    task_store: TaskStoreConfig = Field(default_factory=TaskStoreConfig)
    peers: list[PeerConfig] = Field(default_factory=list)
    logging_level: str = "INFO"
```

- [ ] **Step 9: Create peer manager implementation**

```python
# src/hermes_a2a/peer_manager.py
"""Peer discovery and management for A2A agent communication."""

import asyncio
import logging
from typing import Any

from hermes_a2a.a2a_client import HermesA2AClient, A2AClientError
from hermes_a2a.models import PeerConfig

logger = logging.getLogger(__name__)


class PeerManager:
    """Manages discovery and communication with remote A2A peer agents."""
    
    def __init__(self, peers_config: list[PeerConfig]):
        self.peers_config = peers_config
        self._a2a_client = HermesA2AClient()
        self._discovered_peers: dict[str, dict[str, Any]] = {}
    
    async def close(self) -> None:
        """Close the peer manager and cleanup resources.""" 
        await self._a2a_client.close()
    
    async def list_peers(self) -> list[dict[str, Any]]:
        """List all configured peers with their discovery status."""
        peers = []
        for peer_config in self.peers_config:
            if not peer_config.enabled:
                continue
                
            peer_info = {
                "name": peer_config.name,
                "agent_card_url": peer_config.agent_card_url,
                "status": "unknown",
                "last_seen": None,
            }
            
            # Add discovery info if available
            if peer_config.name in self._discovered_peers:
                discovered = self._discovered_peers[peer_config.name]
                peer_info.update(discovered)
                
            peers.append(peer_info)
            
        return peers
    
    async def discover_all(self) -> list[dict[str, Any]]:
        """Discover all enabled peers and return their status."""
        discovery_tasks = []
        enabled_peers = [p for p in self.peers_config if p.enabled]
        
        for peer_config in enabled_peers:
            task = asyncio.create_task(
                self._discover_peer(peer_config),
                name=f"discover-{peer_config.name}"
            )
            discovery_tasks.append(task)
        
        # Wait for all discoveries (with timeout)
        results = await asyncio.gather(*discovery_tasks, return_exceptions=True)
        
        # Process results
        discovered = []
        for i, result in enumerate(results):
            peer_config = enabled_peers[i]
            
            if isinstance(result, Exception):
                logger.warning("Failed to discover peer %s: %s", peer_config.name, result)
                peer_info = {
                    "name": peer_config.name,
                    "status": "error",
                    "error": str(result),
                }
            else:
                peer_info = result
                self._discovered_peers[peer_config.name] = peer_info
            
            discovered.append(peer_info)
            
        return discovered
    
    async def _discover_peer(self, peer_config: PeerConfig) -> dict[str, Any]:
        """Discover a single peer."""
        try:
            card = await self._a2a_client.discover_agent(peer_config.agent_card_url)
            
            return {
                "name": peer_config.name,
                "status": "available", 
                "agent_name": card.name,
                "agent_version": card.version,
                "last_seen": "now",  # Would be actual timestamp in production
            }
            
        except A2AClientError as exc:
            return {
                "name": peer_config.name, 
                "status": "unreachable",
                "error": str(exc),
            }
    
    async def send_message_to_peer(
        self,
        peer_name: str, 
        message: str,
        context_id: str | None = None
    ) -> dict[str, Any]:
        """Send a message to a named peer."""
        # Find peer config
        peer_config = None
        for p in self.peers_config:
            if p.name == peer_name and p.enabled:
                peer_config = p
                break
        
        if peer_config is None:
            raise ValueError(f"Peer '{peer_name}' not found or disabled")
        
        # Extract base URL from agent card URL
        base_url = peer_config.agent_card_url.replace("/.well-known/agent-card.json", "")
        
        try:
            task = await self._a2a_client.send_message(
                agent_url=base_url,
                message=message,
                context_id=context_id,
                auth_token=peer_config.auth_token or None
            )
            
            return {
                "success": True,
                "task_id": task.id,
                "peer_name": peer_name,
            }
            
        except A2AClientError as exc:
            logger.error("Failed to send message to peer %s: %s", peer_name, exc)
            return {
                "success": False,
                "error": str(exc),
                "peer_name": peer_name,
            }
```

- [ ] **Step 10: Run peer manager tests**

Run: `pytest tests/test_peer_manager.py -v`
Expected: PASS

- [ ] **Step 11: Add peer management routes to server.py**

```python
# In server.py, add peer routes after line 251
# Peer management routes
async def list_peers(request: Request):
    gw = request.app.state.gateway
    manager: PeerManager = gw["peer_manager"]
    peers = await manager.list_peers()
    return Response(content=json.dumps({"peers": peers}), media_type="application/json")

async def discover_peers(request: Request):
    gw = request.app.state.gateway
    manager: PeerManager = gw["peer_manager"]
    discovered = await manager.discover_all()
    return Response(content=json.dumps({"discovered": discovered}), media_type="application/json")

async def relay_message(request: Request):
    gw = request.app.state.gateway
    manager: PeerManager = gw["peer_manager"]
    
    data = await request.json()
    peer_name = data.get("peer_name")
    message = data.get("message")
    context_id = data.get("context_id")
    
    if not peer_name or not message:
        return Response(status_code=400, content="Missing peer_name or message")
    
    result = await manager.send_message_to_peer(peer_name, message, context_id)
    return Response(content=json.dumps(result), media_type="application/json")

# Add routes to app
app.routes.append(Route("/a2a/peers", list_peers, methods=["GET"]))
app.routes.append(Route("/a2a/peers/discover", discover_peers, methods=["POST"]))  
app.routes.append(Route("/a2a/relay", relay_message, methods=["POST"]))

# Add peer manager to app state in create_app()
from hermes_a2a.peer_manager import PeerManager

# In create_app(), after line 127, add:
peer_manager = PeerManager(cfg.peers)

# Add to app_state dict:
app_state = {
    "hermes_client": hermes_client,
    "task_store": task_store,
    "session_store": session_store,
    "handler": handler,
    "peer_manager": peer_manager,  # Add this line
    "config": cfg,
    "start_time": None,
    "metrics": _metrics_counters,
}

# Add cleanup to lifespan
# In lifespan function, before final logger.info():
await peer_manager.close()
```

- [ ] **Step 12: Write integration test for full peer communication**

```python  
# tests/test_peer_manager.py - add integration test
@pytest.mark.asyncio
async def test_end_to_end_peer_communication():
    """Test full peer discovery and message sending flow."""
    from hermes_a2a.server import create_app
    from fastapi.testclient import TestClient
    import yaml
    
    # Create test config with peer
    config_yaml = """
    peers:
      - name: test-peer
        agent_card_url: http://localhost:18801/.well-known/agent-card.json
        enabled: true
    """
    
    with patch('hermes_a2a.config.load_config') as mock_load:
        from hermes_a2a.models import GatewayConfig, PeerConfig
        config = GatewayConfig()
        config.peers = [PeerConfig(
            name="test-peer",
            agent_card_url="http://localhost:18801/.well-known/agent-card.json"
        )]
        mock_load.return_value = config
        
        app = create_app()
        
    # Mock the peer manager's A2A client
    with patch('hermes_a2a.peer_manager.HermesA2AClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_card = AsyncMock()
        mock_card.name = "Test Peer"
        mock_card.version = "1.0"
        mock_client.discover_agent.return_value = mock_card
        
        mock_task = AsyncMock()
        mock_task.id = "task-12345"
        mock_client.send_message.return_value = mock_task
        
        mock_client_class.return_value = mock_client
        
        client = TestClient(app)
        
        # Test peer discovery
        response = client.post("/a2a/peers/discover")
        assert response.status_code == 200
        data = response.json()
        assert len(data["discovered"]) == 1
        assert data["discovered"][0]["name"] == "test-peer"
        assert data["discovered"][0]["status"] == "available"
        
        # Test message relay
        response = client.post("/a2a/relay", json={
            "peer_name": "test-peer",
            "message": "Hello from Hermes!",
            "context_id": "test-ctx-001"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] == True
        assert data["task_id"] == "task-12345"
```

- [ ] **Step 13: Run integration test**

Run: `pytest tests/test_peer_manager.py::test_end_to_end_peer_communication -v`
Expected: PASS

- [ ] **Step 14: Manual test with real servers (optional validation)**

```bash
# Terminal 1: Start hermes-a2a-v1 
cd /Users/lwj04/projects/hermes-a2a-v1
echo 'peers: [{name: "local-test", agent_card_url: "http://localhost:18800/.well-known/agent-card.json"}]' > test-peer.yaml  
HERMES_A2A_CONFIG=test-peer.yaml python -m hermes_a2a

# Terminal 2: Test peer endpoints
curl -s http://localhost:18800/a2a/peers | python -m json.tool
curl -s -X POST http://localhost:18800/a2a/peers/discover | python -m json.tool
```

Expected: See peer discovery working against self

- [ ] **Step 15: Commit**

```bash
git add . 
git commit -m "feat: add A2A client and peer management system

- Create HermesA2AClient wrapper around a2a-sdk Client
- Add PeerManager for peer discovery and message relay  
- Add PeerConfig to configuration models
- Expose /a2a/peers, /a2a/peers/discover, /a2a/relay endpoints
- Add comprehensive tests for peer communication flow
- Enable bidirectional A2A communication with remote agents"
```

---

## Plan Complete

**Execution Handoff:**

Plan complete and saved to `docs/superpowers/plans/2026-05-25-phase-4-openclaw-interop-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints  

**Which approach?**