"""Skill-based routing for selecting the best peer for a task."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PeerScore:
    """Score and match details for a peer against a task."""

    peer_name: str
    score: float = 0.0
    matched_skills: list[str] = field(default_factory=list)
    matched_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_name": self.peer_name,
            "score": self.score,
            "matched_skills": self.matched_skills,
            "matched_tags": self.matched_tags,
        }


class SkillRouter:
    """Routes tasks to peers based on skill matching, tags, history, and load.

    Scoring weights:
      - Skill name matching: 3.0 per match
      - Tag matching: 2.0 per match
      - Historical success rate: up to 1.0
      - Load balancing: up to 1.0 (fewer active tasks = higher score)
    """

    WEIGHT_SKILL = 3.0
    WEIGHT_TAG = 2.0
    WEIGHT_HISTORY = 1.0
    WEIGHT_LOAD = 1.0

    def __init__(self) -> None:
        # Historical success tracking: {peer_name: {"success": int, "total": int}}
        self._history: dict[str, dict[str, int]] = {}
        # Active task tracking: {peer_name: int}
        self._active_tasks: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, task: str, peers: list[dict[str, Any]]) -> list[PeerScore]:
        """Score and rank peers for a given task.

        Args:
            task: The task description string.
            peers: List of peer info dicts. Each should have:
                   - "name": str
                   - "skills": list[dict] (optional, each with "name" key)
                   - "tags": list[str] (optional)

        Returns:
            List of PeerScore sorted by score descending.
        """
        if not peers:
            return []

        # Extract keywords from the task
        task_keywords = self._extract_keywords(task)

        scores: list[PeerScore] = []
        for peer in peers:
            score = self._score_peer(task_keywords, peer)
            scores.append(score)

        # Sort by score descending, then by name for stable ordering
        scores.sort(key=lambda s: (-s.score, s.peer_name))
        return scores

    def record_success(self, peer_name: str) -> None:
        """Record a successful task completion for a peer."""
        hist = self._history.setdefault(peer_name, {"success": 0, "total": 0})
        hist["success"] += 1
        hist["total"] += 1

    def record_failure(self, peer_name: str) -> None:
        """Record a failed task for a peer."""
        hist = self._history.setdefault(peer_name, {"success": 0, "total": 0})
        hist["total"] += 1

    def increment_active(self, peer_name: str) -> None:
        """Increment active task count for a peer."""
        self._active_tasks[peer_name] = self._active_tasks.get(peer_name, 0) + 1

    def decrement_active(self, peer_name: str) -> None:
        """Decrement active task count for a peer."""
        count = self._active_tasks.get(peer_name, 0)
        if count > 0:
            self._active_tasks[peer_name] = count - 1

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score_peer(self, task_keywords: list[str], peer: dict[str, Any]) -> PeerScore:
        """Calculate score for a single peer."""
        peer_name = peer.get("name", "unknown")
        peer_skills = peer.get("skills", [])
        peer_tags = peer.get("tags", [])

        # Extract skill names
        skill_names = [
            s.get("name", "").lower() if isinstance(s, dict) else str(s).lower()
            for s in peer_skills
        ]
        tag_names = [t.lower() for t in peer_tags]

        # Match skills using flexible matching
        matched_skills: list[str] = []
        for kw in task_keywords:
            for sn in skill_names:
                if self._fuzzy_match(kw, sn):
                    if sn not in matched_skills:
                        matched_skills.append(sn)

        # Match tags using flexible matching
        matched_tags: list[str] = []
        for kw in task_keywords:
            for tn in tag_names:
                if self._fuzzy_match(kw, tn):
                    if tn not in matched_tags:
                        matched_tags.append(tn)

        score = 0.0
        score += len(matched_skills) * self.WEIGHT_SKILL
        score += len(matched_tags) * self.WEIGHT_TAG

        # Historical success rate bonus
        hist = self._history.get(peer_name)
        if hist and hist["total"] > 0:
            success_rate = hist["success"] / hist["total"]
            score += success_rate * self.WEIGHT_HISTORY

        # Load balancing: fewer active tasks = higher score
        active = self._active_tasks.get(peer_name, 0)
        # Normalize: 0 active = full bonus, each active task reduces bonus
        load_bonus = max(0.0, self.WEIGHT_LOAD - active * 0.2)
        score += load_bonus

        return PeerScore(
            peer_name=peer_name,
            score=round(score, 4),
            matched_skills=matched_skills,
            matched_tags=matched_tags,
        )

    @staticmethod
    def _fuzzy_match(a: str, b: str) -> bool:
        """Check if two strings are related using prefix overlap.

        Matches if one is a prefix of the other (min 4 chars), or if they share
        a common prefix of at least 5 characters.
        """
        if a == b:
            return True
        if a in b or b in a:
            return True
        # Check common prefix length
        min_len = min(len(a), len(b))
        if min_len < 4:
            return False
        common = 0
        for i in range(min_len):
            if a[i] == b[i]:
                common += 1
            else:
                break
        return common >= min(len(a), len(b), 5)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract lowercase keywords from task text."""
        # Remove common stop words and extract meaningful tokens
        stop_words = {
            "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "shall", "can",
            "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
            "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further", "then",
            "once", "here", "there", "when", "where", "why", "how", "all", "each",
            "every", "both", "few", "more", "most", "other", "some", "such", "no",
            "not", "only", "own", "same", "so", "than", "too", "very", "just",
            "because", "if", "about", "up", "this", "that", "these", "those",
            "it", "its", "i", "me", "my", "we", "our", "you", "your", "he", "him",
            "his", "she", "her", "they", "them", "their", "what", "which", "who",
        }

        # Tokenize: split on non-alphanumeric chars
        tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
        keywords = [t for t in tokens if t and t not in stop_words and len(t) > 1]
        return keywords
