"""A1 block pack section (ADR 0011): multi-member blocks only.

AC8: a block-of-one emits NOTHING new — the pack is byte-stable against the
pre-A1 shape, pinned here by fingerprint equality between an unassigned
activity and the same activity in a block-of-one. A multi-member block adds a
small `block` section (member list + combined totals) to the primary
activity's pack.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.blocks import assign_activity_to_block
from app.services.coach.context import build_context_pack

T0 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)


def _user(db):
    user = User(email=f"pack-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=4, max_hr=190))
    db.commit()
    return user


def _activity(db, user, *, start=T0, elapsed=1500, type="Run", distance=5000):
    a = Activity(user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
                 start_date=start, type=type, name=f"{type}", distance_m=distance,
                 moving_time_s=elapsed, elapsed_time_s=elapsed, elev_gain_m=10.0,
                 avg_hr=140, raw_summary={})
    db.add(a)
    db.commit()
    db.add(DerivedMetric(activity_id=a.id, effort="easy", structure="continuous",
                         duration_class="standard", effort_score=50.0, flags=[],
                         confidence="medium", confidence_reasons=[]))
    db.commit()
    db.refresh(a)
    return a


def test_block_of_one_pack_is_byte_stable(db):
    # AC8: the solo-run pack must be byte-identical before and after block
    # assignment — no `block` key, same fingerprint.
    user = _user(db)
    activity = _activity(db, user)

    before = build_context_pack(db, activity)
    before_fp = before.fingerprint()
    assert "block" not in before.to_serializable_dict()

    assign_activity_to_block(db, activity)
    db.refresh(activity)
    after = build_context_pack(db, activity)

    assert "block" not in after.to_serializable_dict()
    assert after.fingerprint() == before_fp


def test_multi_member_block_adds_block_section_to_primary_pack(db):
    user = _user(db)
    walk = _activity(db, user, start=T0, elapsed=1200, type="Walk", distance=2000)
    block = assign_activity_to_block(db, walk)
    run = _activity(db, user, start=T0 + timedelta(seconds=1800), elapsed=2400,
                    type="Run", distance=8000)
    assert assign_activity_to_block(db, run).id == block.id
    db.refresh(block)

    pack = build_context_pack(db, run)  # the primary
    pack_dict = pack.to_serializable_dict()

    assert "block" in pack_dict
    section = pack_dict["block"]
    assert len(section["members"]) == 2
    types = [m["type"] for m in section["members"]]
    assert types == ["Walk", "Run"]  # chronological
    assert section["combined_duration_s"] == 1200 + 2400
    assert section["combined_distance_m"] == 2000 + 8000
    primaries = [m for m in section["members"] if m["is_primary"]]
    assert len(primaries) == 1 and primaries[0]["type"] == "Run"
