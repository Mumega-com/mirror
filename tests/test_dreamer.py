"""Unit tests for Dreamer scoring and tier logic — no DB needed."""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engram(days_old: float, reference_count: int = 1, pinned: bool = False) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(days=days_old)
    return {
        "id": "test-id",
        "timestamp": ts.isoformat(),
        "memory_tier": "working",
        "importance_score": 1.0,
        "reference_count": reference_count,
        "archived": False,
        "raw_data": {"text": "test content", "pinned": pinned},
    }


# ---------------------------------------------------------------------------
# Scoring formula tests
# ---------------------------------------------------------------------------

def test_score_fresh_engram():
    from scripts.dreamer import score_engram
    # 0 days old, reference_count=1, base_weight=1.0
    # recency = 1/(1+0) = 1.0, score = (1.0*1.0*1)/8 = 0.125
    score = score_engram(_engram(days_old=0))
    assert abs(score - 0.125) < 0.001


def test_score_older_engram():
    from scripts.dreamer import score_engram
    # 10 days old: recency = 1/(1+1.0) = 0.5, score = (1.0*0.5*1)/8 = 0.0625
    score = score_engram(_engram(days_old=10))
    assert abs(score - 0.0625) < 0.001


def test_score_pinned_boosts():
    from scripts.dreamer import score_engram
    # pinned: base_weight=5.0, 0 days old → score = (5.0*1.0*1)/8 = 0.625
    score = score_engram(_engram(days_old=0, pinned=True))
    assert abs(score - 0.625) < 0.001


def test_score_high_reference_count():
    from scripts.dreamer import score_engram
    # reference_count=8, 0 days old → score = (1.0*1.0*8)/8 = 1.0
    score = score_engram(_engram(days_old=0, reference_count=8))
    assert abs(score - 1.0) < 0.001


def test_score_capped_at_reasonable_value():
    from scripts.dreamer import score_engram
    # High ref count + pinned should not error
    score = score_engram(_engram(days_old=0, reference_count=100, pinned=True))
    assert score > 0


def test_score_zero_days():
    from scripts.dreamer import score_engram
    score = score_engram(_engram(days_old=0))
    assert score > 0


def test_score_decreases_with_age():
    from scripts.dreamer import score_engram
    score_new = score_engram(_engram(days_old=0))
    score_old = score_engram(_engram(days_old=30))
    assert score_new > score_old


# ---------------------------------------------------------------------------
# Tier promotion tests
# ---------------------------------------------------------------------------

def test_promote_working_to_episodic():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=1)
    e["memory_tier"] = "working"
    assert promote_tier(e, score=0.5) == "episodic"


def test_no_promote_working_low_score():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=1)
    e["memory_tier"] = "working"
    assert promote_tier(e, score=0.3) == "working"


def test_promote_episodic_to_long_term():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=8)
    e["memory_tier"] = "episodic"
    assert promote_tier(e, score=0.8) == "long_term"


def test_no_promote_episodic_too_young():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=3)
    e["memory_tier"] = "episodic"
    # score > 0.7 but only 3 days old — must be > 7 days
    assert promote_tier(e, score=0.8) == "episodic"


def test_promote_long_term_to_procedural():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=35)
    e["memory_tier"] = "long_term"
    assert promote_tier(e, score=0.9) == "procedural"


def test_no_promote_long_term_too_young():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=10)
    e["memory_tier"] = "long_term"
    assert promote_tier(e, score=0.9) == "long_term"


def test_system_tier_never_promoted():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=0)
    e["memory_tier"] = "system"
    assert promote_tier(e, score=9.9) == "system"


def test_procedural_tier_not_promoted():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=100)
    e["memory_tier"] = "procedural"
    assert promote_tier(e, score=9.9) == "procedural"


def test_promote_boundary_score():
    from scripts.dreamer import promote_tier
    e = _engram(days_old=1)
    e["memory_tier"] = "working"
    # Exactly at boundary — score must be strictly > 0.4
    assert promote_tier(e, score=0.4) == "working"
    assert promote_tier(e, score=0.41) == "episodic"


# ---------------------------------------------------------------------------
# Archive predicate tests
# ---------------------------------------------------------------------------

def test_should_archive_old_low_score():
    from scripts.dreamer import should_archive
    e = _engram(days_old=366)
    assert should_archive(e, score=0.05) is True


def test_no_archive_recent():
    from scripts.dreamer import should_archive
    e = _engram(days_old=10)
    assert should_archive(e, score=0.05) is False


def test_no_archive_high_score():
    from scripts.dreamer import should_archive
    e = _engram(days_old=100)
    assert should_archive(e, score=0.5) is False


def test_no_archive_system_tier():
    from scripts.dreamer import should_archive
    e = _engram(days_old=200)
    e["memory_tier"] = "system"
    assert should_archive(e, score=0.0) is False


def test_archive_boundary_age():
    from scripts.dreamer import should_archive
    e_young = _engram(days_old=364)
    e_old = _engram(days_old=366)
    assert should_archive(e_young, score=0.05) is False
    assert should_archive(e_old, score=0.05) is True


def test_archive_boundary_score():
    from scripts.dreamer import should_archive
    e = _engram(days_old=400)
    # score must be strictly < 0.1
    assert should_archive(e, score=0.1) is False
    assert should_archive(e, score=0.09) is True
