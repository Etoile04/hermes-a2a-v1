"""Tests for ResultAggregator."""
from __future__ import annotations

import pytest

from hermes_a2a.orchestrator import SubTaskResult, SubTaskStatus
from hermes_a2a.result_aggregator import ResultAggregator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completed(subtask_id: str = "st-1", result: str = "done", peer: str = "agent-a") -> SubTaskResult:
    return SubTaskResult(
        subtask_id=subtask_id,
        peer_name=peer,
        status=SubTaskStatus.COMPLETED,
        result=result,
    )


def _failed(subtask_id: str = "st-2", error: str = "crashed", peer: str = "agent-b") -> SubTaskResult:
    return SubTaskResult(
        subtask_id=subtask_id,
        peer_name=peer,
        status=SubTaskStatus.FAILED,
        error=error,
    )


def _timed_out(subtask_id: str = "st-3", peer: str = "agent-c") -> SubTaskResult:
    return SubTaskResult(
        subtask_id=subtask_id,
        peer_name=peer,
        status=SubTaskStatus.TIMED_OUT,
        error="Timed out after 30s",
    )


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------

class TestConcatenate:
    """Tests for the concatenate aggregation strategy."""

    def test_all_completed(self):
        agg = ResultAggregator()
        results = [_completed("1", "Result A"), _completed("2", "Result B")]
        output = agg.aggregate(results, strategy="concatenate")
        assert "Result A" in output
        assert "Result B" in output

    def test_custom_separator(self):
        agg = ResultAggregator()
        results = [_completed("1", "A"), _completed("2", "B")]
        output = agg.aggregate(results, strategy="concatenate", separator=" | ")
        assert output == "A | B"

    def test_with_failures(self):
        agg = ResultAggregator()
        results = [_completed("1", "OK"), _failed("2", "error msg")]
        output = agg.aggregate(results, strategy="concatenate")
        assert "OK" in output
        assert "[ERROR]" in output
        assert "error msg" in output

    def test_with_timeout(self):
        agg = ResultAggregator()
        results = [_timed_out()]
        output = agg.aggregate(results, strategy="concatenate")
        assert "[TIMEOUT]" in output


class TestSummarize:
    """Tests for the summarize aggregation strategy."""

    def test_basic_summary(self):
        agg = ResultAggregator()
        results = [_completed("1", "Section one"), _completed("2", "Section two")]
        output = agg.aggregate(results, strategy="summarize")
        assert "## Results" in output
        assert "Section one" in output
        assert "Section two" in output
        assert "2 completed" in output

    def test_summary_with_errors(self):
        agg = ResultAggregator()
        results = [_completed("1", "ok"), _failed("2", "boom")]
        output = agg.aggregate(results, strategy="summarize")
        assert "## Results" in output
        assert "## Errors" in output
        assert "boom" in output
        assert "1 completed" in output
        assert "1 failed" in output

    def test_summary_with_timeout(self):
        agg = ResultAggregator()
        results = [_timed_out()]
        output = agg.aggregate(results, strategy="summarize")
        assert "Timed Out" in output


class TestVote:
    """Tests for the vote aggregation strategy."""

    def test_unanimous(self):
        agg = ResultAggregator()
        results = [_completed("1", "yes"), _completed("2", "yes"), _completed("3", "yes")]
        output = agg.aggregate(results, strategy="vote")
        assert "Unanimous" in output
        assert "yes" in output

    def test_majority(self):
        agg = ResultAggregator()
        results = [_completed("1", "yes"), _completed("2", "no"), _completed("3", "yes")]
        output = agg.aggregate(results, strategy="vote")
        assert "Majority" in output
        assert "yes" in output

    def test_no_completed_results(self):
        agg = ResultAggregator()
        results = [_failed()]
        output = agg.aggregate(results, strategy="vote")
        assert "No completed results" in output

    def test_vote_with_mixed_results(self):
        agg = ResultAggregator()
        results = [
            _completed("1", "A"),
            _completed("2", "B"),
            _failed("3"),
        ]
        output = agg.aggregate(results, strategy="vote")
        # Should still produce a result from the 2 completed
        assert "A" in output or "B" in output


class TestCustom:
    """Tests for the custom aggregation strategy."""

    def test_custom_function(self):
        agg = ResultAggregator()
        results = [_completed("1", "hello"), _completed("2", "world")]

        def join_upper(results: list[SubTaskResult]) -> str:
            parts = [r.result.upper() for r in results if r.result]
            return "-".join(parts)

        output = agg.aggregate(results, strategy="custom", custom_fn=join_upper)
        assert output == "HELLO-WORLD"

    def test_custom_without_function_raises(self):
        agg = ResultAggregator()
        results = [_completed()]
        with pytest.raises(ValueError, match="custom_fn"):
            agg.aggregate(results, strategy="custom")


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_results(self):
        agg = ResultAggregator()
        output = agg.aggregate([], strategy="concatenate")
        assert output == ""

    def test_unknown_strategy_raises(self):
        agg = ResultAggregator()
        results = [_completed()]
        with pytest.raises(ValueError, match="Unknown"):
            agg.aggregate(results, strategy="nonexistent")
