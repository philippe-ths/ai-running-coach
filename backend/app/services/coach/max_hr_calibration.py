"""#945: durable profile fact revision -- max HR, this slice only.

A stated profile fact (max HR) is set once and then treated as settled, even as
the runner's own training produces evidence the fact has been overtaken. This
module is the deterministic, pure detector for the ONE case where "the evidence
contradicts the stated fact" is arithmetic rather than judgement: a recorded
peak heart rate above the stated ceiling. Either the ceiling is wrong or the
sample is noise -- there is no third reading -- so the check can be conservative
and mechanical rather than a model's opinion.

Deliberately narrow. `current_weekly_km` (a moving average, not a peak) and
`resting_hr`/body figures are NOT handled here and are out of scope for #945:
deciding what counts as strong-enough evidence for a drifting average is a
judgement call the issue itself says is the hard part, and it differs per fact.
Shipping the one unambiguous case (a peak either happened or it did not) rather
than a general "evidence contradicts a fact" framework keeps this detector
honest about what it actually knows.

Never diagnoses, never recomputes `hr_zones`, and never writes anything -- it
only reports what the runner's own data shows. The write happens only if the
runner confirms the `revise_max_hr` proposed action (proposed_actions.py).

Mirrors calibration.py's shape: pure comparison logic, no DB, no I/O. The
DB-reading adapter (recent activities' recorded max HR, the profile's stated
max HR and its own "have we raised this before" bookkeeping) lives in
thread_turn.py, the same split calibration.py and context.py use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

# More than one independent activity must show the exceedance before anything
# is raised. A single spike is a sensor artefact -- the #657 lesson the issue
# itself cites: a durable record written from soft evidence is hard to notice
# and harder to undo, so the bar for RAISING it (not the bar for believing a
# single number) has to be higher than one sample.
MIN_QUALIFYING_ACTIVITIES = 2

# At least this many of the runner's own recent activities need a recorded max
# HR before the detector reasons about them at all. Two qualifying hits out of
# a total sample of two is not "this runner's recent training" -- it is the
# runner's *whole* thin record, which is exactly the kind of soft foundation
# the AC exists to prevent. Mirrors the MIN_HISTORY_FOR_NOVELTY floor used
# elsewhere in the coach layer for the same reason (novelty.py).
MIN_HISTORY_ACTIVITIES = 3

# A recorded peak must clear the stated max by more than this to count as a
# real exceedance. Consumer HR straps/optical sensors routinely carry a few bpm
# of noise, and a 1-2 bpm "exceedance" is exactly that noise, not evidence a
# 3-digit heart rate ceiling is wrong. 5 bpm sits comfortably above typical
# sensor noise while still being a small, conservative bar.
EXCEEDANCE_MARGIN_BPM = 5

# How far back "the runner's own recent activities" reaches. A quarter of
# training is enough to see a genuine shift and short enough that "recent"
# still means recent.
LOOKBACK_DAYS = 90

# Anti-nag (#945 AC5): once a revision has been surfaced/offered, the SAME
# evidence must not be raised again for this many days. A materially higher
# peak arriving later is new evidence and re-raises regardless of the cooldown
# (see `find_max_hr_revision`'s comparison against `last_surfaced_value`).
RESURFACE_COOLDOWN_DAYS = 14

# A recorded max HR outside this band is device/sensor error, not a runner's
# heart rate -- nobody's true max HR sits below a typical resting rate or above
# what is physiologically survivable. Without this floor a couple of glitched
# GPS-watch readings (the kind that occasionally spike into the hundreds of
# extra bpm) could clear MIN_QUALIFYING_ACTIVITIES and this detector would
# offer to write garbage into the runner's record. Readings outside the band
# are dropped before anything else runs, not merely excluded from "exceeding":
# a corrupted sample should not count as evidence of anything, including
# ordinary history.
_PLAUSIBLE_HR_FLOOR_BPM = 40
_PLAUSIBLE_HR_CEILING_BPM = 230


@dataclass(frozen=True)
class MaxHrRevisionFinding:
    """One instance of "your own data has overtaken your stated max HR"."""

    stated_max: int
    suggested_max: int
    margin_bpm: int
    exceeding_count: int
    sample_count: int
    basis: str


def find_max_hr_revision(
    stated_max: Optional[int],
    observed_maxes: List[Optional[float]],
    *,
    last_surfaced_value: Optional[float] = None,
    last_surfaced_at: Optional[datetime] = None,
    as_of: Optional[datetime] = None,
) -> Optional[MaxHrRevisionFinding]:
    """Compare the runner's stated max HR to their own recent recorded peaks.

    Abstains (returns None) whenever the evidence is not strong enough to
    raise, which is the default:

    - no stated max to compare against;
    - fewer than `MIN_HISTORY_ACTIVITIES` recent activities carry a recorded
      max HR at all (too little history to reason about);
    - fewer than `MIN_QUALIFYING_ACTIVITIES` of those exceed the stated max by
      more than `EXCEEDANCE_MARGIN_BPM` (a single spike is noise, not
      evidence);
    - this same evidence was already surfaced within `RESURFACE_COOLDOWN_DAYS`
      and nothing new has come in since (the anti-nag property -- see AC5).

    `suggested_max` is the highest of the qualifying recorded peaks: once two
    or more independent activities clear the bar, the highest one actually
    recorded is real evidence the runner's ceiling is at least that high, not
    a guess.
    """
    if not stated_max or stated_max <= 0:
        return None

    samples = [
        m
        for m in observed_maxes
        if m is not None and _PLAUSIBLE_HR_FLOOR_BPM <= m <= _PLAUSIBLE_HR_CEILING_BPM
    ]
    if len(samples) < MIN_HISTORY_ACTIVITIES:
        return None

    exceeding = [m for m in samples if m >= stated_max + EXCEEDANCE_MARGIN_BPM]
    if len(exceeding) < MIN_QUALIFYING_ACTIVITIES:
        return None

    suggested = int(round(max(exceeding)))
    margin = suggested - stated_max

    if last_surfaced_value is not None and suggested <= last_surfaced_value:
        if last_surfaced_at is not None:
            now = as_of or datetime.now(timezone.utc)
            surfaced = last_surfaced_at
            # SQLite (the test DB) drops tzinfo on round-trip even though the
            # column is declared timezone-aware; Postgres (prod) does not. A
            # naive value here always means the UTC it was written in.
            if surfaced.tzinfo is None:
                surfaced = surfaced.replace(tzinfo=timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            elapsed_days = (now - surfaced).total_seconds() / 86400
            if elapsed_days < RESURFACE_COOLDOWN_DAYS:
                return None

    return MaxHrRevisionFinding(
        stated_max=int(stated_max),
        suggested_max=suggested,
        margin_bpm=margin,
        exceeding_count=len(exceeding),
        sample_count=len(samples),
        basis=(
            f"{len(exceeding)} of the last {len(samples)} recorded runs show a "
            f"peak heart rate at or above {suggested} bpm, {margin} bpm over the "
            f"stated max of {int(stated_max)} bpm."
        ),
    )


def gather_max_hr_revision(
    db: Session, user_id: UUID, *, respect_cooldown: bool = True
) -> Optional[MaxHrRevisionFinding]:
    """The DB-reading half: this runner's stated max HR and their own recent
    recorded peaks, fed through `find_max_hr_revision`.

    Mirrors `services/readiness.py`'s split (a pure core plus one DB-reading
    function in the same module, rather than scattering the query into every
    caller) rather than calibration.py's fully-pure shape, because here there
    are two callers -- the thread baseline and the proposed-action offer --
    that must read the exact same evidence and the exact same anti-nag state,
    and a query duplicated in both places is a query that can drift between
    them. Read-only: never writes the anti-nag bookkeeping it reads.

    `respect_cooldown=False` skips the anti-nag suppression and answers only
    "does the raw evidence still support a revision at all" -- for the ONE
    caller that must ask that question about the SAME evidence it just
    stamped: `_execute`'s re-check right before writing the confirmed
    revision. Every other caller (surfacing the fact, building a new offer)
    leaves it at the default, because THOSE are exactly the places the
    cooldown exists to govern.
    """
    from app.models.activity import Activity
    from app.models.user_profile import UserProfile

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile is None or not profile.max_hr:
        return None

    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    rows = (
        db.query(Activity.max_hr)
        .filter(
            Activity.user_id == user_id,
            Activity.is_deleted.is_(False),
            Activity.start_date >= since,
        )
        .order_by(Activity.start_date.desc())
        .all()
    )
    observed = [row[0] for row in rows]
    last_surfaced_value = (
        profile.max_hr_revision_last_surfaced_value if respect_cooldown else None
    )
    last_surfaced_at = (
        profile.max_hr_revision_last_surfaced_at if respect_cooldown else None
    )
    return find_max_hr_revision(
        profile.max_hr,
        observed,
        last_surfaced_value=last_surfaced_value,
        last_surfaced_at=last_surfaced_at,
    )


def record_surfaced(db: Session, user_id: UUID, value: int) -> None:
    """Stamp the anti-nag bookkeeping: this value was just put in front of the
    runner as an offer. The ONLY writer of these two columns besides the
    runner's own confirmed revision clearing them; called exactly once, from
    proposed_actions._build_offer, only when a `revise_max_hr` offer is
    actually minted -- never from a read path (the thread baseline, the
    read-only diagram capture script), so looking at this fact never nags on
    its own.
    """
    from app.models.user_profile import UserProfile

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile is None:
        return
    profile.max_hr_revision_last_surfaced_value = value
    profile.max_hr_revision_last_surfaced_at = datetime.now(timezone.utc)
    db.commit()
