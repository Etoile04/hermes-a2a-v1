"""Result aggregation for combining subtask outputs."""
from __future__ import annotations

import logging
from collections import Counter
from typing import Callable

from hermes_a2a.orchestrator import SubTaskResult, SubTaskStatus

logger = logging.getLogger(__name__)


class ResultAggregator:
    """Aggregates subtask results using various strategies.

    Strategies:
      - "concatenate": join results with a separator
      - "summarize": return a summary with section headers
      - "vote": return the majority answer
      - "custom": use a provided callable
    """

    def aggregate(
        self,
        results: list[SubTaskResult],
        strategy: str = "concatenate",
        separator: str = "\n\n",
        custom_fn: Callable[[list[SubTaskResult]], str] | None = None,
    ) -> str:
        """Merge subtask results using the specified strategy.

        Args:
            results: List of SubTaskResult objects.
            strategy: Aggregation strategy name.
            separator: Separator for "concatenate" strategy.
            custom_fn: Callable for "custom" strategy.

        Returns:
            Aggregated result string.
        """
        if not results:
            return ""

        if strategy == "concatenate":
            return self._concatenate(results, separator)
        elif strategy == "summarize":
            return self._summarize(results)
        elif strategy == "vote":
            return self._vote(results)
        elif strategy == "custom":
            if custom_fn is None:
                raise ValueError("custom_fn must be provided for 'custom' strategy")
            return custom_fn(results)
        else:
            raise ValueError(f"Unknown aggregation strategy: {strategy}")

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _concatenate(self, results: list[SubTaskResult], separator: str) -> str:
        """Join all successful results, noting failures."""
        parts: list[str] = []
        for r in results:
            if r.status == SubTaskStatus.COMPLETED and r.result is not None:
                parts.append(r.result)
            elif r.status == SubTaskStatus.TIMED_OUT:
                parts.append(f"[TIMEOUT] Subtask {r.subtask_id}: timed out")
            elif r.status == SubTaskStatus.FAILED:
                error_msg = r.error or "unknown error"
                parts.append(f"[ERROR] Subtask {r.subtask_id}: {error_msg}")
            else:
                parts.append(f"[PENDING] Subtask {r.subtask_id}: no result yet")

        return separator.join(parts) if parts else ""

    def _summarize(self, results: list[SubTaskResult]) -> str:
        """Create a summary with section headers for each result."""
        lines: list[str] = []
        completed = [r for r in results if r.status == SubTaskStatus.COMPLETED]
        failed = [
            r
            for r in results
            if r.status in (SubTaskStatus.FAILED, SubTaskStatus.TIMED_OUT)
        ]
        pending = [
            r for r in results
            if r.status not in (SubTaskStatus.COMPLETED, SubTaskStatus.FAILED, SubTaskStatus.TIMED_OUT)
        ]

        lines.append(f"# Summary ({len(completed)} completed, {len(failed)} failed, {len(pending)} pending)")
        lines.append("")

        if completed:
            lines.append("## Results")
            for r in completed:
                peer = r.peer_name or "unknown"
                lines.append(f"### Peer: {peer} (subtask {r.subtask_id})")
                if r.result:
                    lines.append(r.result)
                lines.append("")

        if failed:
            lines.append("## Errors")
            for r in failed:
                status_label = "Timed Out" if r.status == SubTaskStatus.TIMED_OUT else "Failed"
                error_msg = r.error or "unknown error"
                lines.append(f"- Subtask {r.subtask_id} ({r.peer_name}): {status_label} — {error_msg}")
            lines.append("")

        if pending:
            lines.append("## Pending")
            for r in pending:
                lines.append(f"- Subtask {r.subtask_id} ({r.peer_name}): {r.status}")

        return "\n".join(lines)

    def _vote(self, results: list[SubTaskResult]) -> str:
        """Return the majority answer among completed results."""
        completed_results = [
            r.result for r in results if r.status == SubTaskStatus.COMPLETED and r.result is not None
        ]

        if not completed_results:
            # Check for failed/timed out results
            failed = [
                r for r in results
                if r.status in (SubTaskStatus.FAILED, SubTaskStatus.TIMED_OUT)
            ]
            if failed:
                errors = "; ".join(r.error or "unknown" for r in failed)
                return f"No completed results to vote on. Errors: {errors}"
            return "No completed results to vote on."

        # Count occurrences
        counter = Counter(completed_results)
        winner, count = counter.most_common(1)[0]
        total = len(completed_results)

        if count == total:
            return f"Unanimous ({total}/{total}): {winner}"
        else:
            return f"Majority ({count}/{total}): {winner}"
