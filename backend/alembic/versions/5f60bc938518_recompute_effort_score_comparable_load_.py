"""recompute_effort_score_comparable_load_186

One-time backfill (#186): rescale every stored ``derived_metrics.effort_score``
onto the new comparable training-load primitive so the whole history sits on a
single scale. Before this, the with-HR branch (``minutes × (avg/peak)³ × 10``)
and the no-HR branch (raw ``minutes``) produced values on different scales, so
summing load across a mixed history (what the acute/chronic model does) was
invalid. The new primitive is Edwards "zone-minutes": ``Σ (minutes in zone z)
× z``, with z the HR zone 1-5 (or its best per-tier estimate).

The recompute reads only already-stored data — the row's ``time_in_zones``, the
activity's ``moving_time_s``/``avg_hr``, the runner's profile max HR, and the
check-in RPE — so it needs no Strava streams and is cheap. The formula below is
a FROZEN copy of ``services.analysis.metrics.compute_training_load`` as of this
revision (migrations must not import app code that can later change).

Reversible: downgrade restores the prior two-branch formula from the same
columns.

Revision ID: 5f60bc938518
Revises: a7c3f1e9b2d4
Create Date: 2026-06-15 17:09:01.830413

"""
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f60bc938518'
down_revision: Union[str, None] = 'a7c3f1e9b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- Frozen snapshot of the #186 primitive (do not import app code) ---

_ZONE_WEIGHT = {"Z1": 1, "Z2": 2, "Z3": 3, "Z4": 4, "Z5": 5}
_DEFAULT_BAND = 2  # no HR, no RPE: population-typical aerobic (zone 2)


def _band_from_pct(pct: float) -> int:
    if pct < 0.60:
        return 1
    if pct < 0.70:
        return 2
    if pct < 0.80:
        return 3
    if pct < 0.90:
        return 4
    return 5


def _rpe_to_band(rpe: int) -> int:
    return min(5, max(1, (rpe + 1) // 2))


def _new_load(
    moving_time_s: Optional[float],
    zones: Optional[dict],
    avg_hr: Optional[float],
    athlete_max_hr: Optional[int],
    rpe: Optional[int],
) -> float:
    minutes = (moving_time_s or 0) / 60.0
    if zones:
        zone_items = [(z, sec) for z, sec in zones.items() if z in _ZONE_WEIGHT]
        zone_seconds = sum(sec for _, sec in zone_items)
        if zone_seconds > 0:
            weighted = sum((sec / 60.0) * _ZONE_WEIGHT[z] for z, sec in zone_items)
            sub_zone_minutes = max(0.0, minutes - zone_seconds / 60.0)
            return round(weighted + sub_zone_minutes, 1)
    if minutes <= 0:
        return 0.0
    if avg_hr and athlete_max_hr and athlete_max_hr > 0:
        return round(minutes * _band_from_pct(avg_hr / athlete_max_hr), 1)
    if rpe:
        return round(minutes * _rpe_to_band(rpe), 1)
    return round(minutes * _DEFAULT_BAND, 1)


def _old_load(
    moving_time_s: Optional[float],
    avg_hr: Optional[float],
    activity_max_hr: Optional[float],
) -> float:
    # The pre-#186 two-branch formula (avg_hr / activity-recorded-peak)³.
    if avg_hr and activity_max_hr:
        hr_ratio = avg_hr / activity_max_hr
        minutes = (moving_time_s or 0) / 60.0
        return round(minutes * (hr_ratio ** 3) * 10, 1)
    return round((moving_time_s or 0) / 60.0, 1)


def _resolve_athlete_max(profile_max_hr) -> int:
    # Mirror the orchestrator: profile max only when plausibly real, else 190.
    if profile_max_hr and profile_max_hr > 100:
        return int(profile_max_hr)
    return 190


def _coerce_zones(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    import json
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


_SELECT_NEW = sa.text(
    """
    SELECT dm.activity_id AS activity_id,
           dm.time_in_zones AS time_in_zones,
           a.moving_time_s AS moving_time_s,
           a.avg_hr AS avg_hr,
           up.max_hr AS profile_max_hr,
           (SELECT c.rpe FROM check_ins c
             WHERE c.activity_id = a.id
             ORDER BY c.created_at
             LIMIT 1) AS rpe
    FROM derived_metrics dm
    JOIN activities a ON a.id = dm.activity_id
    LEFT JOIN user_profiles up ON up.user_id = a.user_id
    """
)

_SELECT_OLD = sa.text(
    """
    SELECT dm.activity_id AS activity_id,
           a.moving_time_s AS moving_time_s,
           a.avg_hr AS avg_hr,
           a.max_hr AS activity_max_hr
    FROM derived_metrics dm
    JOIN activities a ON a.id = dm.activity_id
    """
)

_UPDATE = sa.text(
    "UPDATE derived_metrics SET effort_score = :score WHERE activity_id = :aid"
)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(_SELECT_NEW).mappings().all()
    for r in rows:
        score = _new_load(
            r["moving_time_s"],
            _coerce_zones(r["time_in_zones"]),
            r["avg_hr"],
            _resolve_athlete_max(r["profile_max_hr"]),
            r["rpe"],
        )
        conn.execute(_UPDATE, {"score": score, "aid": r["activity_id"]})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(_SELECT_OLD).mappings().all()
    for r in rows:
        score = _old_load(r["moving_time_s"], r["avg_hr"], r["activity_max_hr"])
        conn.execute(_UPDATE, {"score": score, "aid": r["activity_id"]})
