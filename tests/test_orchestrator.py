"""Tests for OrchestrationEngine."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from hermes_a2a.orchestrator import (
    OrchestrationEngine,
    OrchestrationResult,
    OrchestrationStatus,
    SubTask,
    SubTaskResult,
    SubTaskStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PEERS = [
    {"name": "agent-a"},
    {"name": "agent-b"},
    {"name": "agent-c"},
]


def _make_subtasks(n: int, prefix: str = "Task") -> list[SubTask]:
    return [SubTask(description=f"{prefix} {i}") for i in range(n)]


# ---------------------------------------------------------------------------
# Decomposition tests
# ---------------------------------------------------------------------------

class TestDecompose:
    """Tests for OrchestrationEngine.decompose."""

    def test_sequential_splits_sentences(self):
        engine = OrchestrationEngine()
        text = "Analyze the data. Generate a report. Send it to the team."
        subtasks = engine.decompose(text, strategy="sequential")
        assert len(subtasks) >= 2
        assert all(s.status == SubTaskStatus.PENDING for s in subtasks)
        assert all(s.description for s in subtasks)

    def test_parallel_splits_sentences(self):
        engine = OrchestrationEngine()
        text = "Translate to French. Translate to Spanish. Translate to German."
        subtasks = engine.decompose(text, strategy="parallel")
        assert len(subtasks) >= 2

    def test_map_reduce_splits_sections(self):
        engine = OrchestrationEngine()
        text = "Section one content.\n\nSection two content.\n\nSection three content."
        subtasks = engine.decompose(text, strategy="map_reduce")
        assert len(subtasks) >= 2
        assert all(isinstance(s, SubTask) for s in subtasks)

    def test_single_sentence_produces_one_subtask(self):
        engine = OrchestrationEngine()
        subtasks = engine.decompose("Just one thing to do", strategy="sequential")
        assert len(subtasks) == 1
        assert subtasks[0].description == "Just one thing to do"

    def test_subtask_has_unique_id(self):
        engine = OrchestrationEngine()
        subtasks = engine.decompose("First. Second. Third.", strategy="sequential")
        ids = [s.id for s in subtasks]
        assert len(ids) == len(set(ids)), "All subtask IDs must be unique"

    def test_empty_string_produces_default_subtask(self):
        engine = OrchestrationEngine()
        subtasks = engine.decompose("   ", strategy="sequential")
        # Should handle gracefully
        assert all(isinstance(s, SubTask) for s in subtasks)


# ---------------------------------------------------------------------------
# Execution tests
# ---------------------------------------------------------------------------

class TestExecute:
    """Tests for OrchestrationEngine.execute."""

    @pytest.mark.asyncio
    async def test_execute_parallel_with_mock_executor(self):
        engine = OrchestrationEngine()
        subtasks = _make_subtasks(3)
        executor = AsyncMock(return_value="done")
        result = await engine.execute(subtasks, PEERS, executor=executor, strategy="parallel")
        assert result.status == OrchestrationStatus.COMPLETED
        assert result.completed_count == 3
        assert result.failed_count == 0
        assert executor.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_sequential_with_mock_executor(self):
        engine = OrchestrationEngine()
        subtasks = _make_subtasks(2)
        executor = AsyncMock(return_value="ok")
        result = await engine.execute(subtasks, PEERS, executor=executor, strategy="sequential")
        assert result.status == OrchestrationStatus.COMPLETED
        assert result.completed_count == 2

    @pytest.mark.asyncio
    async def test_execute_no_peers_fails(self):
        engine = OrchestrationEngine()
        subtasks = _make_subtasks(2)
        result = await engine.execute(subtasks, [], strategy="parallel")
        assert result.status == OrchestrationStatus.FAILED
        assert result.failed_count == 2
        assert all("No peers available" in (r.error or "") for r in result.subtask_results)

    @pytest.mark.asyncio
    async def test_execute_no_subtasks_succeeds(self):
        engine = OrchestrationEngine()
        result = await engine.execute([], PEERS, strategy="parallel")
        assert result.status == OrchestrationStatus.COMPLETED
        assert len(result.subtask_results) == 0

    @pytest.mark.asyncio
    async def test_execute_default_executor_simulates(self):
        engine = OrchestrationEngine()
        subtasks = _make_subtasks(2)
        result = await engine.execute(subtasks, PEERS, executor=None, strategy="parallel")
        assert result.status == OrchestrationStatus.COMPLETED
        assert all(r.result is not None for r in result.subtask_results)

    @pytest.mark.asyncio
    async def test_execute_partial_failure(self):
        engine = OrchestrationEngine(max_retries=0)
        subtasks = _make_subtasks(3)

        call_count = 0

        async def flaky_executor(peer: str, desc: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("peer crashed")
            return "ok"

        result = await engine.execute(subtasks, PEERS, executor=flaky_executor, strategy="parallel")
        assert result.status == OrchestrationStatus.PARTIAL
        assert result.completed_count >= 1
        assert result.failed_count >= 1

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        engine = OrchestrationEngine(timeout_per_subtask=0.1, max_retries=0)

        async def slow_executor(peer: str, desc: str) -> str:
            await asyncio.sleep(10)
            return "too late"

        subtasks = _make_subtasks(1)
        result = await engine.execute(subtasks, PEERS, executor=slow_executor, strategy="parallel")
        assert result.failed_count == 1
        assert result.subtask_results[0].status == SubTaskStatus.TIMED_OUT

    @pytest.mark.asyncio
    async def test_execute_round_robin_assignment(self):
        engine = OrchestrationEngine()
        subtasks = _make_subtasks(4)

        async def capture_executor(peer: str, desc: str) -> str:
            return peer

        result = await engine.execute(
            subtasks, PEERS, executor=capture_executor, strategy="parallel"
        )
        # 4 subtasks across 3 peers: a, b, c, a
        peer_assignments = [r.result for r in result.subtask_results]
        assert peer_assignments[0] == "agent-a"
        assert peer_assignments[1] == "agent-b"
        assert peer_assignments[2] == "agent-c"
        assert peer_assignments[3] == "agent-a"

    @pytest.mark.asyncio
    async def test_execute_retry_on_failure(self):
        engine = OrchestrationEngine(max_retries=2, retry_delay=0.01, timeout_per_subtask=5.0)
        subtasks = _make_subtasks(1)

        attempt = 0

        async def retry_executor(peer: str, desc: str) -> str:
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise ConnectionError("temporary failure")
            return "success on third try"

        result = await engine.execute(subtasks, PEERS, executor=retry_executor, strategy="sequential")
        assert result.completed_count == 1
        assert result.subtask_results[0].result == "success on third try"


# ---------------------------------------------------------------------------
# Result / dataclass tests
# ---------------------------------------------------------------------------

class TestOrchestrationResult:
    """Tests for OrchestrationResult properties."""

    def test_completed_count(self):
        result = OrchestrationResult(
            subtask_results=[
                SubTaskResult(status=SubTaskStatus.COMPLETED),
                SubTaskResult(status=SubTaskStatus.FAILED),
                SubTaskResult(status=SubTaskStatus.COMPLETED),
            ]
        )
        assert result.completed_count == 2
        assert result.failed_count == 1
        assert result.pending_count == 0

    def test_to_dict(self):
        result = OrchestrationResult(
            task_id="abc",
            status=OrchestrationStatus.COMPLETED,
            subtask_results=[SubTaskResult(subtask_id="x", result="ok")],
            aggregate_result="all done",
        )
        d = result.to_dict()
        assert d["task_id"] == "abc"
        assert d["status"] == "completed"
        assert d["aggregate_result"] == "all done"
        assert len(d["subtask_results"]) == 1
