"""Structural invariant: the serialized context pack carries no duplicated content.

The pack assembles ~20 sections, several of which carry overlapping VIEWS of the
same underlying training facts (e.g. `recent_training` describes the runner's
recent volume while `training_volume` verdicts it vs the norm). Those are
deliberate reframings: the same number serving two jobs in two differently-shaped
sections. They are NOT what this test polices.

What this test forbids is genuine WASTE: an entire non-trivial sub-object (or
list) appearing BYTE-IDENTICALLY at two different places in the serialized pack —
the kind of overlap that was hunted down by hand in #451 (the retired
`recent_training_summary`) and the `RecentComparison.current_all/current_runs`
trim. That is the durable "can't happen again" guarantee.

It is intentionally scoped to byte-identical SUB-OBJECTS, not to repeated scalar
values across sections (most cross-section scalar repeats are the intentional
reframings above; a guard on those would be false-positive noise on day one) and
not to the rendered system prompt (its addenda DELIBERATELY restate the safety
floor for reinforcement — deduping that would fight the design).

To make a byte-identical repeat unambiguously structural and never a data
coincidence, the fixture seeds deliberately VARIED history: every activity has a
distinct distance/time/effort/HR, so two windows or two rows are never equal by
accident. Any duplicate the detector finds is therefore the same datum emitted
twice, which is the thing we forbid.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.models import (
    Activity,
    Block,
    CheckIn,
    CoachReport,
    DerivedMetric,
    UserProfile,
)
from app.services.coach.context import build_context_pack
from app.services.coach.prompt_features import fullest_message_prompt_id

# The FULLEST prompt: it turns on every gated section, so the pack is at its fullest
# and the duplication surface is largest. Derived from the manifest (not pinned to a
# version) so this guard AUTO-TRACKS the newest gated section — a new section added
# under a new prompt id is exercised here the moment it lands, instead of slipping
# through until someone remembers to bump the constant.
FULL_PROMPT_ID = fullest_message_prompt_id()

# A subtree must clear BOTH thresholds to count: enough leaf content that an
# identical copy is meaningful waste, and enough serialized bytes that it is not a
# tiny shared shape like {} or [].
_MIN_LEAVES = 3
_MIN_CANONICAL_LEN = 60


def _canonical(node) -> str:
    return json.dumps(node, sort_keys=True, default=str)


def _count_leaves(node) -> int:
    """Number of non-null scalar leaves in a subtree."""
    if isinstance(node, dict):
        return sum(_count_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count_leaves(v) for v in node)
    return 0 if node is None else 1


def _is_descendant(path: str, ancestor: str) -> bool:
    return path.startswith(ancestor + ".") or path.startswith(ancestor + "[")


def find_duplicate_subtrees(pack: dict):
    """Return {canonical_json: [path, path, ...]} for every non-trivial sub-object
    or list that appears byte-identically at two or more distinct paths.

    Only MAXIMAL duplicates are returned: when a whole sub-object is duplicated its
    inner pieces are duplicated too, but reporting the outermost one is enough to
    point at the waste (and keeps a failure message readable). An inner duplicate
    group is suppressed when every one of its paths sits inside a path of a larger
    duplicate group."""
    seen: dict[str, list[str]] = {}

    def walk(node, path: str):
        if isinstance(node, (dict, list)):
            if _count_leaves(node) >= _MIN_LEAVES:
                canon = _canonical(node)
                if len(canon) >= _MIN_CANONICAL_LEN:
                    seen.setdefault(canon, []).append(path)
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            else:
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")

    walk(pack, "")
    groups = {canon: paths for canon, paths in seen.items() if len(paths) >= 2}

    # Drop any group nested entirely inside a larger (longer-canonical) group.
    maximal = {}
    for canon, paths in groups.items():
        contained = any(
            len(other) > len(canon)
            and all(any(_is_descendant(p, op) for op in other_paths) for p in paths)
            for other, other_paths in groups.items()
        )
        if not contained:
            maximal[canon] = paths
    return maximal


def _seed_history(db, user_id, *, count=18, anchor: datetime):
    """Seed `count` activities ending at `anchor`, every one with a DISTINCT
    distance/time/effort/HR so no two windows or rows can coincide by accident.
    Spread across ~200 days so the 7d / 30d / previous_30d / norm windows all fill.
    Returns the activities oldest-first."""
    activities = []
    for i in range(count):
        # Spread the oldest runs far back and pack a few into the recent windows.
        days_ago = int((count - i) * 11)  # 198d .. 11d, all distinct
        start = anchor - timedelta(days=days_ago, hours=(i % 5))
        a = Activity(
            id=uuid.uuid4(),
            user_id=user_id,
            strava_activity_id=900000 + i,
            name=f"Run {i}",
            type="Run" if i % 4 else "Walk",  # a little modality variety
            start_date=start,
            distance_m=5000 + i * 317,        # distinct per i
            moving_time_s=1800 + i * 123,     # distinct per i
            elapsed_time_s=1900 + i * 130,
            avg_hr=130.0 + i * 1.7,           # distinct per i
            max_hr=175.0 + i,
            avg_cadence=168.0 + i * 0.5,
            elev_gain_m=20.0 + i * 3,
            average_speed_mps=2.5 + i * 0.03,
        )
        db.add(a)
        db.flush()
        db.add(
            DerivedMetric(
                id=uuid.uuid4(),
                activity_id=a.id,
                effort=("easy", "moderate", "tempo", "hard")[i % 4],
                duration_class="standard",
                structure="continuous",
                is_hilly=bool(i % 3),
                is_race=False,
                effort_score=3.0 + i * 0.7,       # distinct per i
                pace_variability=0.05 + i * 0.001,
                hr_drift=0.02 + i * 0.002,
                time_in_zones={"Z1": 600 + i, "Z2": 1200 + i, "Z3": 300 + i},
                flags=[],
                confidence="high",
                confidence_reasons=[],
            )
        )
        activities.append(a)
    db.flush()
    return activities


def _make_report(db, activity, *, headline, lead, step):
    """A non-fallback CoachReport with a distinct digest + next_steps, so the
    longitudinal / adherence sections populate with unique content."""
    next_steps = [{"action": step, "details": f"{step} detail for {headline}"}]
    db.add(
        CoachReport(
            id=uuid.uuid4(),
            activity_id=activity.id,
            prompt_id=FULL_PROMPT_ID,
            schema_version="2.0",
            report={"message": f"{headline} body", "next_steps": next_steps},
            meta={},
            context_pack={},
            digest={
                "activity_date": activity.start_date.date().isoformat(),
                "headline": headline,
                "lead_argument": lead,
                "next_steps": [step],
            },
            is_fallback=False,
        )
    )
    db.flush()


def test_serialized_pack_has_no_duplicate_subtrees(db, enable_durable_memory):
    user_id = uuid.uuid4()
    anchor = datetime(2026, 6, 24, 18, 0, tzinfo=timezone.utc)  # a Wednesday, mid-week

    db.add(
        UserProfile(
            user_id=user_id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=35,
            max_hr=190,
            max_hr_source="profile",
        )
    )
    db.flush()

    history = _seed_history(db, user_id, count=18, anchor=anchor)

    # Two prior reports with distinct content -> populate longitudinal + adherence.
    _make_report(
        db, history[-3], headline="Solid aerobic block", lead="EF trending up",
        step="hold easy pace",
    )
    _make_report(
        db, history[-2], headline="Good tempo execution", lead="paced the reps well",
        step="add one quality session",
    )

    # The subject: the most recent run, placed in a 2-member block (a brick) so the
    # block aggregate populates, with a stored stream_view so that section fills too.
    subject = history[-1]
    sibling = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=999001,
        name="Brick walk",
        type="Walk",
        start_date=subject.start_date + timedelta(minutes=10),
        distance_m=2100,
        moving_time_s=900,
        elapsed_time_s=950,
        avg_hr=110.0,
        max_hr=130.0,
        avg_cadence=120.0,
        elev_gain_m=5.0,
        average_speed_mps=2.33,
    )
    db.add(sibling)
    db.flush()
    block = Block(
        id=uuid.uuid4(),
        user_id=user_id,
        start_date=subject.start_date,
        end_date=sibling.start_date,
        primary_activity_id=subject.id,
    )
    db.add(block)
    db.flush()
    subject.block_id = block.id
    sibling.block_id = block.id
    db.add(
        DerivedMetric(
            id=uuid.uuid4(),
            activity_id=sibling.id,
            effort="recovery",
            duration_class="short",
            structure="continuous",
            is_hilly=False,
            is_race=False,
            effort_score=1.5,
            flags=[],
            confidence="high",
            confidence_reasons=[],
        )
    )
    db.flush()

    # Give the subject a distinct stored stream view (deep=True under v11 pulls it).
    subj_metric = (
        db.query(DerivedMetric).filter(DerivedMetric.activity_id == subject.id).first()
    )
    subj_metric.stream_view = {
        "hr": [140, 145, 150, 148, 152],
        "pace": [5.1, 5.0, 4.9, 5.0, 4.8],
        "grade": [0.0, 1.0, -1.0, 0.5, 0.0],
        "cadence": [168, 170, 172, 169, 171],
    }
    db.flush()

    # A check-in so the perceived_effort + check_in sections carry content.
    db.add(
        CheckIn(
            id=uuid.uuid4(),
            activity_id=subject.id,
            rpe=6,
            pain_score=1,
            pain_location="left knee",
            sleep_quality=4,
            notes="legs felt heavy at the start",
        )
    )
    db.flush()

    pack = build_context_pack(db, subject, prompt_id=FULL_PROMPT_ID)
    serialized = pack.to_serializable_dict()

    # Guard against this test silently going vacuous: the duplication guarantee is
    # only meaningful if the overlap-prone sections are actually populated. If a
    # refactor stops emitting one of these, fail loudly here rather than passing on
    # an empty pack.
    must_be_present = {
        "metrics", "longitudinal", "perceived_effort", "adherence", "block",
        "corpus", "stance", "training_load", "training_volume", "stream_view",
        "recent_training", "training_history",
    }
    missing = must_be_present - serialized.keys()
    assert not missing, f"fixture did not populate expected sections: {sorted(missing)}"

    duplicates = find_duplicate_subtrees(serialized)

    assert not duplicates, (
        "The serialized context pack carries duplicated content — the same "
        "non-trivial sub-object appears at multiple paths. Either fold the "
        "duplicate into one section or trim it (see #451 / the RecentComparison "
        "trim for precedent):\n"
        + "\n".join(
            f"  duplicated at {paths}:\n    {canon[:300]}"
            for canon, paths in duplicates.items()
        )
    )


def test_detector_flags_an_injected_duplicate():
    """The detector itself has teeth: an injected identical sub-object is caught,
    and trivial shared shapes ({} / short lists) are not."""
    big = {"a": 1, "b": 2, "c": 3, "d": "a reasonably long string value here"}
    pack = {
        "section_one": {"nested": big},
        "section_two": {"nested": dict(big)},  # byte-identical copy
        "trivial_a": {},
        "trivial_b": {},  # identical but trivial -> ignored
        "small_a": {"x": 1},
        "small_b": {"x": 1},  # identical but below the leaf/length floor -> ignored
    }
    dupes = find_duplicate_subtrees(pack)
    # Only the MAXIMAL duplicate is reported (the outer `{"nested": big}` objects),
    # not the inner `big` copies nested inside them.
    assert len(dupes) == 1
    (paths,) = dupes.values()
    assert set(paths) == {".section_one", ".section_two"}
