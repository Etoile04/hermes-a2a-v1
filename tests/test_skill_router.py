"""Tests for SkillRouter."""
from __future__ import annotations

import pytest

from hermes_a2a.skill_router import PeerScore, SkillRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PEER_TRANSLATOR = {
    "name": "translator-agent",
    "skills": [{"name": "translation"}, {"name": "language-detection"}],
    "tags": ["nlp", "multilingual"],
}

PEER_CODER = {
    "name": "coder-agent",
    "skills": [{"name": "code-generation"}, {"name": "debugging"}],
    "tags": ["python", "programming"],
}

PEER_GENERAL = {
    "name": "general-agent",
    "skills": [{"name": "general"}],
    "tags": ["general"],
}


# ---------------------------------------------------------------------------
# Scoring and ranking tests
# ---------------------------------------------------------------------------

class TestRoute:
    """Tests for SkillRouter.route."""

    def test_empty_peers(self):
        router = SkillRouter()
        result = router.route("translate text", [])
        assert result == []

    def test_skill_matching(self):
        router = SkillRouter()
        scores = router.route(
            "Translate this text to French",
            [PEER_TRANSLATOR, PEER_CODER],
        )
        # Translator should score higher
        assert scores[0].peer_name == "translator-agent"
        assert "translation" in scores[0].matched_skills or "language-detection" in scores[0].matched_skills

    def test_tag_matching(self):
        router = SkillRouter()
        scores = router.route(
            "Help with Python programming",
            [PEER_TRANSLATOR, PEER_CODER],
        )
        # Coder should score higher due to tag match
        assert scores[0].peer_name == "coder-agent"
        assert any("programming" in t or "python" in t for t in scores[0].matched_tags)

    def test_returns_peer_scores(self):
        router = SkillRouter()
        scores = router.route("do something", [PEER_TRANSLATOR, PEER_CODER, PEER_GENERAL])
        assert len(scores) == 3
        assert all(isinstance(s, PeerScore) for s in scores)
        # Should be sorted by score descending
        for i in range(len(scores) - 1):
            assert scores[i].score >= scores[i + 1].score

    def test_peer_without_skills_or_tags(self):
        router = SkillRouter()
        peer_minimal = {"name": "minimal-agent"}
        scores = router.route("any task", [peer_minimal])
        assert len(scores) == 1
        assert scores[0].peer_name == "minimal-agent"
        # Should still get a load-balancing bonus
        assert scores[0].score >= 0


class TestHistoryAndLoad:
    """Tests for historical success rate and load balancing."""

    def test_history_increases_score(self):
        router = SkillRouter()
        # Give translator a good history
        for _ in range(5):
            router.record_success("translator-agent")
        router.record_failure("coder-agent")

        scores = router.route("some task", [PEER_TRANSLATOR, PEER_CODER])
        translator_score = next(s.score for s in scores if s.peer_name == "translator-agent")
        coder_score = next(s.score for s in scores if s.peer_name == "coder-agent")
        assert translator_score > coder_score

    def test_load_balancing_prefers_less_loaded(self):
        router = SkillRouter()
        # Simulate load on translator
        for _ in range(5):
            router.increment_active("translator-agent")

        scores = router.route("some task", [PEER_TRANSLATOR, PEER_CODER])
        # Both have no skill/tag match for "some task", so load decides
        coder = next(s for s in scores if s.peer_name == "coder-agent")
        translator = next(s for s in scores if s.peer_name == "translator-agent")
        assert coder.score > translator.score

    def test_decrement_active(self):
        router = SkillRouter()
        router.increment_active("agent-a")
        router.increment_active("agent-a")
        router.decrement_active("agent-a")
        assert router._active_tasks.get("agent-a") == 1

    def test_record_success_failure_tracking(self):
        router = SkillRouter()
        router.record_success("agent-a")
        router.record_success("agent-a")
        router.record_failure("agent-a")
        hist = router._history["agent-a"]
        assert hist["success"] == 2
        assert hist["total"] == 3

    def test_peer_score_to_dict(self):
        score = PeerScore(
            peer_name="test-agent",
            score=5.5,
            matched_skills=["skill1"],
            matched_tags=["tag1"],
        )
        d = score.to_dict()
        assert d["peer_name"] == "test-agent"
        assert d["score"] == 5.5
        assert d["matched_skills"] == ["skill1"]
        assert d["matched_tags"] == ["tag1"]
