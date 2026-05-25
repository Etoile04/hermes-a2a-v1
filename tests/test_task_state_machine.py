"""Tests for TaskStateMachine — state transition validation."""

from __future__ import annotations

import pytest
from a2a.types.a2a_pb2 import TaskState

from hermes_a2a.task_state_machine import InvalidStateTransitionError, TaskStateMachine


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def sm():
    """Fresh TaskStateMachine instance."""
    return TaskStateMachine()


# ==================================================================
# validate_transition — valid transitions
# ==================================================================

class TestValidTransitions:
    """Every transition listed in the A2A spec should be accepted."""

    @pytest.mark.parametrize("from_state, to_state", [
        # SUBMITTED →
        (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING),
        (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_FAILED),
        (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_CANCELED),
        # WORKING →
        (TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED),
        (TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_FAILED),
        (TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_CANCELED),
        (TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_INPUT_REQUIRED),
        # INPUT_REQUIRED →
        (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_WORKING),
        (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_CANCELED),
    ])
    def test_valid_transition(self, sm, from_state, to_state):
        """Should not raise for a spec-compliant transition."""
        sm.validate_transition(from_state, to_state)


# ==================================================================
# validate_transition — invalid transitions
# ==================================================================

class TestInvalidTransitions:

    @pytest.mark.parametrize("from_state, to_state", [
        # Terminal states → anything (should reject all)
        (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_WORKING),
        (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_CANCELED),
        (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_WORKING),
        (TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_WORKING),
        # Reverse / disallowed
        (TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_SUBMITTED),
        (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_COMPLETED),
        (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_COMPLETED),
        (TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_FAILED),
    ])
    def test_invalid_transition_raises(self, sm, from_state, to_state):
        """Disallowed transitions must raise InvalidStateTransitionError."""
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            sm.validate_transition(from_state, to_state)
        # Error should carry both states
        err = exc_info.value
        assert err.from_state == from_state
        assert err.to_state == to_state


# ==================================================================
# can_cancel
# ==================================================================

class TestCanCancel:

    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_INPUT_REQUIRED,
    ])
    def test_cancelable_states(self, sm, state):
        assert sm.can_cancel(state) is True

    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
        TaskState.TASK_STATE_UNSPECIFIED,
    ])
    def test_non_cancelable_states(self, sm, state):
        assert sm.can_cancel(state) is False


# ==================================================================
# is_terminal
# ==================================================================

class TestIsTerminal:

    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    ])
    def test_terminal_states(self, sm, state):
        assert sm.is_terminal(state) is True

    @pytest.mark.parametrize("state", [
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
        TaskState.TASK_STATE_UNSPECIFIED,
    ])
    def test_non_terminal_states(self, sm, state):
        assert sm.is_terminal(state) is False


# ==================================================================
# Handler integration: on_cancel_task respects terminal states
# ==================================================================

class TestHandlerCancelIntegration:
    """Verify that HermesA2AHandler uses the state machine for cancellation."""

    @pytest.fixture
    async def handler(self, mock_hermes_client, task_store):
        from hermes_a2a.a2a_handler import HermesA2AHandler
        h = HermesA2AHandler(mock_hermes_client, task_store)
        return h

    @pytest.fixture
    def _make_params(self):
        """Helper to build a CancelTaskRequest-like object."""
        from unittest.mock import MagicMock
        def _factory(task_id: str):
            p = MagicMock()
            p.id = task_id
            return p
        return _factory

    async def test_cancel_completed_task_returns_none(
        self, handler, task_store, _make_params
    ):
        """A task already in COMPLETED state should NOT be canceled."""
        task_dict = {
            "id": "task-done",
            "contextId": "ctx-1",
            "status": {"state": "completed"},
            "response": "done",
        }
        ctx = None
        await task_store.save(task_dict, ctx)

        result = await handler.on_cancel_task(_make_params("task-done"), ctx)
        assert result is None

        # Verify the store was NOT updated to "canceled"
        stored = await task_store.get("task-done", ctx)
        assert stored["status"]["state"] == "completed"

    async def test_cancel_working_task_succeeds(
        self, handler, task_store, _make_params
    ):
        """A task in WORKING state should be cancelable."""
        task_dict = {
            "id": "task-wip",
            "contextId": "ctx-2",
            "status": {"state": "working"},
            "response": None,
        }
        ctx = None
        await task_store.save(task_dict, ctx)

        result = await handler.on_cancel_task(_make_params("task-wip"), ctx)
        assert result is not None

        # Verify state was updated to canceled
        stored = await task_store.get("task-wip", ctx)
        assert stored["status"]["state"] == "canceled"

    async def test_cancel_failed_task_returns_none(
        self, handler, task_store, _make_params
    ):
        """A task in FAILED state should NOT be canceled."""
        task_dict = {
            "id": "task-fail",
            "contextId": "ctx-3",
            "status": {"state": "failed"},
        }
        ctx = None
        await task_store.save(task_dict, ctx)

        result = await handler.on_cancel_task(_make_params("task-fail"), ctx)
        assert result is None

        stored = await task_store.get("task-fail", ctx)
        assert stored["status"]["state"] == "failed"

    async def test_cancel_canceled_task_returns_none(
        self, handler, task_store, _make_params
    ):
        """A task already CANCELED should not be re-canceled."""
        task_dict = {
            "id": "task-canceled",
            "contextId": "ctx-4",
            "status": {"state": "canceled"},
        }
        ctx = None
        await task_store.save(task_dict, ctx)

        result = await handler.on_cancel_task(_make_params("task-canceled"), ctx)
        assert result is None

    async def test_cancel_submitted_task_succeeds(
        self, handler, task_store, _make_params
    ):
        """A task in SUBMITTED state should be cancelable."""
        task_dict = {
            "id": "task-sub",
            "contextId": "ctx-5",
            "status": {"state": "submitted"},
        }
        ctx = None
        await task_store.save(task_dict, ctx)

        result = await handler.on_cancel_task(_make_params("task-sub"), ctx)
        assert result is not None

        stored = await task_store.get("task-sub", ctx)
        assert stored["status"]["state"] == "canceled"

    async def test_cancel_input_required_task_succeeds(
        self, handler, task_store, _make_params
    ):
        """A task in INPUT_REQUIRED state should be cancelable."""
        task_dict = {
            "id": "task-input",
            "contextId": "ctx-6",
            "status": {"state": "input_required"},
        }
        ctx = None
        await task_store.save(task_dict, ctx)

        result = await handler.on_cancel_task(_make_params("task-input"), ctx)
        assert result is not None

        stored = await task_store.get("task-input", ctx)
        assert stored["status"]["state"] == "canceled"
