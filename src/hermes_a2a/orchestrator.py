"""Orchestration engine for multi-agent task decomposition and execution."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SubTaskStatus(str, Enum):
    """Status of a subtask."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class OrchestrationStatus(str, Enum):
    """Status of the overall orchestration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class SubTask:
    """A single subtask within an orchestrated workflow."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    description: str = ""
    assigned_peer: str | None = None
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "assigned_peer": self.assigned_peer,
            "status": self.status.value if isinstance(self.status, SubTaskStatus) else self.status,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class SubTaskResult:
    """Result of a completed subtask."""

    subtask_id: str = ""
    peer_name: str = ""
    status: SubTaskStatus = SubTaskStatus.COMPLETED
    result: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "peer_name": self.peer_name,
            "status": self.status.value if isinstance(self.status, SubTaskStatus) else self.status,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class OrchestrationResult:
    """Result of an orchestrated multi-agent task."""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: OrchestrationStatus = OrchestrationStatus.PENDING
    subtask_results: list[SubTaskResult] = field(default_factory=list)
    aggregate_result: str | None = None

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.subtask_results if r.status == SubTaskStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(
            1 for r in self.subtask_results if r.status in (SubTaskStatus.FAILED, SubTaskStatus.TIMED_OUT)
        )

    @property
    def pending_count(self) -> int:
        return sum(1 for r in self.subtask_results if r.status == SubTaskStatus.PENDING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value if isinstance(self.status, OrchestrationStatus) else self.status,
            "subtask_results": [r.to_dict() for r in self.subtask_results],
            "aggregate_result": self.aggregate_result,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "pending_count": self.pending_count,
        }


# Type for the peer executor callable
PeerExecutor = Callable[[str, str], Any]


class OrchestrationEngine:
    """Decomposes complex tasks into subtasks and orchestrates execution across peers."""

    def __init__(
        self,
        timeout_per_subtask: float = 30.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ):
        self.timeout_per_subtask = timeout_per_subtask
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    # ------------------------------------------------------------------
    # Decomposition
    # ------------------------------------------------------------------

    def decompose(self, task_description: str, strategy: str = "sequential") -> list[SubTask]:
        """Split a complex task description into subtasks based on the given strategy.

        Strategies:
          - "sequential": split by sentence/line boundaries
          - "parallel": split by sentence/line boundaries (same split, execution differs)
          - "map_reduce": split into sections for distributed processing

        Returns a list of SubTask objects with PENDING status.
        """
        if strategy == "map_reduce":
            return self._decompose_map_reduce(task_description)
        # Both "sequential" and "parallel" use sentence-based splitting
        return self._decompose_sentences(task_description)

    def _decompose_sentences(self, text: str) -> list[SubTask]:
        """Split text by sentences or lines into subtasks."""
        # Try splitting by numbered items first (e.g., "1. Do X")
        import re

        numbered = re.split(r"(?=(?:^|\n)\s*\d+\.\s)", text.strip())
        if len(numbered) > 1:
            parts = [p.strip() for p in numbered if p.strip()]
        else:
            # Split by newlines or sentence-ending punctuation
            parts = re.split(r"(?<=[.!?\n])\s+", text.strip())
            parts = [p.strip() for p in parts if p.strip()]

        if not parts:
            parts = [text.strip()]

        return [
            SubTask(
                description=part,
                status=SubTaskStatus.PENDING,
            )
            for part in parts
        ]

    def _decompose_map_reduce(self, text: str) -> list[SubTask]:
        """Split text into sections for map-reduce processing."""
        import re

        # Split by paragraphs (double newlines)
        sections = re.split(r"\n\s*\n", text.strip())
        if len(sections) <= 1:
            # Fallback: split by sentences
            sections = re.split(r"(?<=[.!?])\s+", text.strip())

        parts = [s.strip() for s in sections if s.strip()]
        if not parts:
            parts = [text.strip()]

        return [
            SubTask(
                description=part,
                status=SubTaskStatus.PENDING,
            )
            for part in parts
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        subtasks: list[SubTask],
        peers: list[dict[str, Any]],
        executor: PeerExecutor | None = None,
        strategy: str = "sequential",
    ) -> OrchestrationResult:
        """Execute subtasks by distributing them across available peers.

        Args:
            subtasks: List of SubTask objects to execute.
            peers: List of peer info dicts, each with at least 'name' key.
            executor: Async callable (peer_name, task_description) -> str.
                      If None, subtasks are marked completed with mock results.
            strategy: "sequential", "parallel", or "map_reduce".

        Returns:
            OrchestrationResult with status and individual results.
        """
        result = OrchestrationResult(
            status=OrchestrationStatus.RUNNING,
            subtask_results=[],
        )

        if not subtasks:
            result.status = OrchestrationStatus.COMPLETED
            return result

        if not peers:
            # No peers available — mark all subtasks as failed
            for st in subtasks:
                result.subtask_results.append(
                    SubTaskResult(
                        subtask_id=st.id,
                        status=SubTaskStatus.FAILED,
                        error="No peers available",
                    )
                )
            result.status = OrchestrationStatus.FAILED
            return result

        # Assign peers round-robin
        assigned = self._assign_peers(subtasks, peers)

        if strategy == "sequential":
            await self._execute_sequential(assigned, result, executor)
        else:
            # "parallel" and "map_reduce" both fan out concurrently
            await self._execute_parallel(assigned, result, executor)

        # Determine final status
        self._finalize_status(result)
        return result

    def _assign_peers(
        self, subtasks: list[SubTask], peers: list[dict[str, Any]]
    ) -> list[tuple[SubTask, str]]:
        """Assign peers to subtasks round-robin."""
        assignments: list[tuple[SubTask, str]] = []
        peer_names = [p["name"] for p in peers]
        for i, st in enumerate(subtasks):
            peer_name = peer_names[i % len(peer_names)]
            st.assigned_peer = peer_name
            assignments.append((st, peer_name))
        return assignments

    async def _execute_sequential(
        self,
        assignments: list[tuple[SubTask, str]],
        result: OrchestrationResult,
        executor: PeerExecutor | None,
    ) -> None:
        """Execute subtasks one by one."""
        for st, peer_name in assignments:
            st.status = SubTaskStatus.RUNNING
            sub_result = await self._run_subtask(st, peer_name, executor)
            result.subtask_results.append(sub_result)

    async def _execute_parallel(
        self,
        assignments: list[tuple[SubTask, str]],
        result: OrchestrationResult,
        executor: PeerExecutor | None,
    ) -> None:
        """Execute subtasks concurrently."""
        tasks = []
        for st, peer_name in assignments:
            st.status = SubTaskStatus.RUNNING
            tasks.append(self._run_subtask(st, peer_name, executor))

        sub_results = await asyncio.gather(*tasks, return_exceptions=True)
        for sr in sub_results:
            if isinstance(sr, BaseException):
                result.subtask_results.append(
                    SubTaskResult(
                        status=SubTaskStatus.FAILED,
                        error=str(sr),
                    )
                )
            else:
                result.subtask_results.append(sr)

    async def _run_subtask(
        self,
        subtask: SubTask,
        peer_name: str,
        executor: PeerExecutor | None,
    ) -> SubTaskResult:
        """Run a single subtask with timeout and retry."""
        last_error: str | None = None

        for attempt in range(self.max_retries + 1):
            try:
                if executor is not None:
                    raw = await asyncio.wait_for(
                        executor(peer_name, subtask.description),
                        timeout=self.timeout_per_subtask,
                    )
                    output = str(raw)
                else:
                    # No executor — simulate success
                    output = f"Result for: {subtask.description[:50]}"

                subtask.status = SubTaskStatus.COMPLETED
                subtask.result = output
                return SubTaskResult(
                    subtask_id=subtask.id,
                    peer_name=peer_name,
                    status=SubTaskStatus.COMPLETED,
                    result=output,
                )
            except asyncio.TimeoutError:
                last_error = f"Timed out after {self.timeout_per_subtask}s"
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                    continue
                subtask.status = SubTaskStatus.TIMED_OUT
                subtask.error = last_error
                return SubTaskResult(
                    subtask_id=subtask.id,
                    peer_name=peer_name,
                    status=SubTaskStatus.TIMED_OUT,
                    error=last_error,
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                    continue
                subtask.status = SubTaskStatus.FAILED
                subtask.error = last_error
                return SubTaskResult(
                    subtask_id=subtask.id,
                    peer_name=peer_name,
                    status=SubTaskStatus.FAILED,
                    error=last_error,
                )

        # Should not reach here, but just in case
        subtask.status = SubTaskStatus.FAILED
        subtask.error = last_error
        return SubTaskResult(
            subtask_id=subtask.id,
            peer_name=peer_name,
            status=SubTaskStatus.FAILED,
            error=last_error,
        )

    def _finalize_status(self, result: OrchestrationResult) -> None:
        """Determine the final orchestration status from subtask results."""
        total = len(result.subtask_results)
        if total == 0:
            result.status = OrchestrationStatus.COMPLETED
            return

        completed = result.completed_count
        failed = result.failed_count

        if completed == total:
            result.status = OrchestrationStatus.COMPLETED
        elif failed == total:
            result.status = OrchestrationStatus.FAILED
        else:
            result.status = OrchestrationStatus.PARTIAL
