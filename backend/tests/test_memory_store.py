"""M1 — runner memory store (DB layer over `runner_memory`) + markdown render.

The thin store: round-trip a profile, upsert replaces the single per-user row in
place, get returns None for an unknown user, and the pure markdown render emits
all five headings even when empty. No LLM here.
"""

import uuid

from app.models.runner_memory import RunnerMemory
from app.models.user import User
from app.schemas.coach_memory import (
    MEMORY_SECTION_TITLES,
    RunnerMemoryProfile,
)
from app.services.coach.memory_store import (
    get_memory,
    render_profile_markdown,
    upsert_memory,
)


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def test_get_returns_none_for_unknown_user(db):
    assert get_memory(db, uuid.uuid4()) is None


def test_round_trips_a_profile(db):
    uid = _user(db)
    profile = RunnerMemoryProfile(
        goals_and_plans=["Valencia half, sub-1:45, October"],
        lately=["Open thread: agreed to try a metronome on easy runs"],
    )

    upsert_memory(db, uid, profile=profile, model_id="claude-haiku-4-5", source_report_count=3)

    row = get_memory(db, uid)
    assert row is not None
    assert row.model_id == "claude-haiku-4-5"
    assert row.source_report_count == 3
    stored = RunnerMemoryProfile.model_validate(row.profile)
    assert stored.goals_and_plans == ["Valencia half, sub-1:45, October"]
    assert stored.who_you_are == []


def test_upsert_replaces_in_place_one_row_per_user(db):
    uid = _user(db)

    upsert_memory(db, uid, profile=RunnerMemoryProfile(goals_and_plans=["old goal"]))
    upsert_memory(db, uid, profile=RunnerMemoryProfile(goals_and_plans=["new goal"]))

    rows = db.query(RunnerMemory).filter(RunnerMemory.user_id == uid).all()
    assert len(rows) == 1
    stored = RunnerMemoryProfile.model_validate(rows[0].profile)
    assert stored.goals_and_plans == ["new goal"]


def test_upsert_accepts_string_user_id(db):
    uid = _user(db)
    upsert_memory(db, str(uid), profile=RunnerMemoryProfile(who_you_are=["runs early"]))
    assert get_memory(db, str(uid)) is not None


def test_render_emits_all_five_headings_when_empty():
    markdown = render_profile_markdown(RunnerMemoryProfile())
    for title in MEMORY_SECTION_TITLES.values():
        assert f"## {title}" in markdown
    # An empty section renders a placeholder, not a phantom bullet.
    assert "_(nothing yet)_" in markdown


def test_render_lists_section_lines():
    markdown = render_profile_markdown(
        RunnerMemoryProfile(limits_and_constraints=["Left knee niggle, mentioned once"])
    )
    assert "## Limits & constraints" in markdown
    assert "- Left knee niggle, mentioned once" in markdown
