"""TaskStateMachine — validates A2A task state transitions per the spec."""

from __future__ import annotations

from a2a.types.a2a_pb2 import TaskState


class InvalidStateTransitionError(Exception):
    """Raised when a task state transition is not allowed by the A2A spec."""

    def __init__(self, from_state: int, to_state: int) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid state transition: {from_state} → {to_state}"
        )


class TaskStateMachine:
    """Encapsulates A2A-valid task state transitions.

    Uses TaskState int constants as keys/values throughout.
    """

    _VALID_TRANSITIONS: dict[int, set[int]] = {
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
    }

    _TERMINAL_STATES: set[int] = {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    }

    _CANCELABLE_STATES: set[int] = {
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_INPUT_REQUIRED,
    }

    # String → TaskState int mapping (store uses lowercase strings)
    _STATE_STR_MAP: dict[str, int] = {
        "unspecified": TaskState.TASK_STATE_UNSPECIFIED,
        "submitted": TaskState.TASK_STATE_SUBMITTED,
        "working": TaskState.TASK_STATE_WORKING,
        "completed": TaskState.TASK_STATE_COMPLETED,
        "failed": TaskState.TASK_STATE_FAILED,
        "canceled": TaskState.TASK_STATE_CANCELED,
        "input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
        "rejected": TaskState.TASK_STATE_REJECTED,
        "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED,
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_transition(self, from_state: int, to_state: int) -> None:
        """Raise InvalidStateTransitionError if *from_state* → *to_state* is disallowed."""
        allowed = self._VALID_TRANSITIONS.get(from_state)
        if allowed is None or to_state not in allowed:
            raise InvalidStateTransitionError(from_state, to_state)

    def can_cancel(self, current_state: int) -> bool:
        """Return True if a task in *current_state* may be canceled."""
        return current_state in self._CANCELABLE_STATES

    def is_terminal(self, state: int) -> bool:
        """Return True if *state* is a terminal (no further transitions)."""
        return state in self._TERMINAL_STATES

    @classmethod
    def state_from_str(cls, state_str: str) -> int:
        """Map a lowercase state string to its TaskState int value.

        Returns TASK_STATE_UNSPECIFIED for unknown strings.
        """
        return cls._STATE_STR_MAP.get(state_str, TaskState.TASK_STATE_UNSPECIFIED)
