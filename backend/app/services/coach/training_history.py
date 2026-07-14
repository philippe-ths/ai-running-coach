"""#561: the multi-year training-history picture for the coach pack.

Accumulated training history changes how a run should be read. Two runners with
near-identical lifetime volume can be completely different athletes — 20 km/week
for 10 years versus 100 km/week for 2 years (~10,400 km each) — and a coach should
know which one it is talking to. The thing that tells them apart is not any single
total but the SHAPE of volume over time and the durability it implies.

This section carries that shape at a deliberately DECAYING resolution (a
level-of-detail ladder): the detailed near term is already owned IN FULL by the recent
window — the original ladder begins after `recent_training`'s 0-60d, while the ADR 0026
Slice 2 grouped_v2 ladder (`recent_weeks_bound=True`) begins after `recent_weeks`'s ~2
weeks (a 14-60d bridging bucket fills the gap) and additionally carries a modality-agnostic
weekly LOAD rate, a per-type split, and calendar bounds so cross-training is never read as
running mileage. Either way it WIDENS as it goes back — 2-6 months, 6-12 months, 1-2
years, 2-5 years, then an open 5+ years tail. Each
bucket reports an AVERAGE WEEKLY rate (not a raw total), so buckets of unequal width
stay directly comparable, and the average divides by the weeks the bucket actually
spans within the runner's history (the volume.py clamp idiom) so a partially-covered
bucket is not deflated. A bucket is emitted only when it holds real data, so the
ladder self-sizes to how deep the history reaches.

On top of the ladder sit the durability TRAITS (the eval-able headline): training
age, peak sustained weekly volume, current-vs-peak (the relationship only — the raw
current weekly volume is NOT restated here, it lives once in
`recent_training.last_30d`, #451 one-lane), multi-year trajectory direction, and
time held at the current load. Each is a deterministic FACT the coach may cite; none
is an intensity verdict and none overrides this run's re-derived DerivedMetric or the
safety floor. Comparisons abstain (`no_norm`/null) when history is too thin to
resolve them.

Pure functions over already-fetched `ActivityFact`s (no DB, no LLM); the DB read
and pack wiring live in `context.py`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, List, Optional, Tuple

from app.schemas.coach_context import (
    TrainingHistoryBucket,
    TrainingHistoryContext,
    TrainingHistoryTraits,
    TrainingHistoryTypeShare,
)

# The richness-decaying ladder: (start_days_ago, end_days_ago, label). The 0-60d
# window is intentionally absent — `recent_training` owns it in FULL (its last_7d,
# last_30d and previous_30d windows all live within 0-60d), so this ladder begins
# exactly where that section ends and never double-covers it (#451 one-lane).
# `end=None` is the open tail (everything older than the prior bound).
_BUCKETS: Tuple[Tuple[int, Optional[int], str], ...] = (
    (60, 180, "2-6 months ago"),
    (180, 365, "6-12 months ago"),
    (365, 730, "1-2 years ago"),
    (730, 1825, "2-5 years ago"),
    (1825, None, "5+ years ago"),
)

# ADR 0026 Slice 2 (#670): the grouped_v2 ladder. `recent_weeks` owns only the last ~2
# weeks day by day, so the coarse ladder must begin at 14 days (not 60) or the 14-60d
# training-response window would reach the coach through NO section. Option A: add ONE
# bridging bucket (14-60d) and keep the rest of the coarse ladder unchanged, so the ladder
# stays coarse and does not re-answer what day-resolved `recent_weeks` already covers.
_BUCKETS_2WK: Tuple[Tuple[int, Optional[int], str], ...] = (
    (14, 60, "2 weeks - 2 months ago"),
) + _BUCKETS

# Emit the section at all only when there is real history beyond the recent window:
# below this, `recent_training`/`training_volume` already say everything there is to
# say, so the section is dropped (None) rather than emitting an empty shell.
_MIN_HISTORY_DAYS = 42
_MIN_ACTIVITIES = 8
# A bucket needs at least this many activities AND this much spanned history to be
# emitted, so neither one stray old activity nor a thin sliver at the very edge of
# history (e.g. the first 12 days of training landing in a "1-2 years ago" bucket)
# paints a misleading far-horizon picture.
_MIN_BUCKET_ACTIVITIES = 2
_MIN_BUCKET_SPAN_DAYS = 21
# The year-over-year trajectory compares the recent 12 months against the PRIOR 12
# months. It abstains unless the prior window holds at least this much real history,
# so a runner with ~1 year of data does not get a "trend" computed against a
# two-week sliver that happens to fall in the prior-year window.
_MIN_TRAJECTORY_PRIOR_DAYS = 180

_SUSTAINED_WEEKS = 4          # the rolling window defining a "sustained" weekly volume
_CURRENT_WEEKS = 4           # the trailing window defining the runner's current load
_TRAJECTORY_DEADBAND_PCT = 15.0  # recent-vs-prior 12mo within this reads as in_line
_LOAD_BAND_PCT = 25.0        # trailing-4wk rate within this of current counts as "same load"
_MAX_WEEKS = 522             # ~10 years; bounds the weekly-series work for deep histories


def _age_days(fact: Any, as_of: date) -> int:
    return (as_of - fact.local_date).days


def _is_run(fact: Any) -> bool:
    return (getattr(fact, "activity_type", "") or "").lower() == "run"


def _distance(fact: Any) -> float:
    return fact.distance_m or 0


def _load(fact: Any) -> float:
    return getattr(fact, "effort_score", None) or 0


def _month_year(as_of: date, days_ago: int) -> str:
    """The calendar month-year `days_ago` before `as_of`, e.g. "Jul 2025". Month-year
    (not an exact day) suits a coarse decaying ladder — a "2-5 years ago" bucket reads
    naturally as a month range, unlike day-resolved `recent_weeks`."""
    return (as_of - timedelta(days=days_ago)).strftime("%b %Y")


def _by_type_shares(
    in_bucket: List[Any], weeks: float
) -> List[TrainingHistoryTypeShare]:
    """Per-activity-type weekly rate + session share within a bucket, most-frequent-first
    (ties broken by type name), so a cross-training block (a ski/bike week) is never read
    as running mileage. Mirrors `recent_weeks._by_type_totals`, in the ladder's weekly-rate
    framing."""
    groups: dict = {}
    for f in in_bucket:
        t = (f.activity_type or "unknown")
        g = groups.setdefault(t, {"sessions": 0, "distance_m": 0.0})
        g["sessions"] += 1
        g["distance_m"] += _distance(f)
    total = len(in_bucket)
    out: List[TrainingHistoryTypeShare] = []
    for t in sorted(groups, key=lambda k: (-groups[k]["sessions"], k)):
        g = groups[t]
        out.append(
            TrainingHistoryTypeShare(
                type=t,
                avg_weekly_distance_m=int(g["distance_m"] / weeks),
                avg_weekly_sessions=round(g["sessions"] / weeks, 2),
                share_pct=round(g["sessions"] / total * 100.0, 1),
            )
        )
    return out


def _bucket(
    facts: List[Any],
    as_of: date,
    first_age: int,
    start: int,
    end: Optional[int],
    label: str,
    *,
    enriched: bool = False,
) -> Optional[TrainingHistoryBucket]:
    """One ladder bucket as an average WEEKLY rate, or None when it holds too little.

    The averaging denominator is the weeks the bucket actually spans WITHIN history:
    the bucket window [start, end) in age-days is clamped to the runner's first
    activity (`first_age` = age of the oldest activity), so a bucket only partially
    covered by history is not deflated by dividing across pre-history days.

    `enriched` (grouped_v2, ADR 0026 Slice 2) additionally populates the modality-agnostic
    weekly LOAD rate, the per-type split, and the calendar bounds; left None on the original
    ladder (and surgically dropped there) so every prior prompt stays byte-identical."""
    covered_end = first_age + 1 if end is None else min(end, first_age + 1)
    span_days = covered_end - start
    if span_days < _MIN_BUCKET_SPAN_DAYS:  # too thin a sliver to name as a bucket
        return None
    in_bucket = [
        f for f in facts if start <= _age_days(f, as_of) < (covered_end if end is None else end)
    ]
    if len(in_bucket) < _MIN_BUCKET_ACTIVITIES:
        return None
    weeks = span_days / 7.0
    runs = sum(1 for f in in_bucket if _is_run(f))
    total_distance = sum(_distance(f) for f in in_bucket)
    return TrainingHistoryBucket(
        label=label,
        start_days_ago=start,
        end_days_ago=covered_end,
        weeks=round(weeks, 1),
        avg_weekly_distance_m=int(total_distance / weeks),
        avg_weekly_sessions=round(len(in_bucket) / weeks, 2),
        run_share_pct=round(runs / len(in_bucket) * 100.0, 1),
        from_date=_month_year(as_of, covered_end) if enriched else None,
        to_date=_month_year(as_of, start) if enriched else None,
        avg_weekly_load=(
            int(sum(_load(f) for f in in_bucket) / weeks) if enriched else None
        ),
        by_type=_by_type_shares(in_bucket, weeks) if enriched else None,
    )


def _weekly_distance_back(facts: List[Any], as_of: date, n_weeks: int) -> List[float]:
    """Weekly distance totals indexed BACK from `as_of`: index 0 is the most recent 7
    days (as_of-6..as_of), index 1 the 7 days before that, and so on for `n_weeks`."""
    series = [0.0] * n_weeks
    for f in facts:
        age = _age_days(f, as_of)
        if age < 0:
            continue
        wk = age // 7
        if 0 <= wk < n_weeks:
            series[wk] += _distance(f)
    return series


def _weekly_load_back(facts: List[Any], as_of: date, n_weeks: int) -> List[float]:
    """Weekly effort_score (training load) totals indexed BACK from `as_of`, same
    indexing as `_weekly_distance_back` — the modality-agnostic load series behind the
    grouped_v2 load traits."""
    series = [0.0] * n_weeks
    for f in facts:
        age = _age_days(f, as_of)
        if age < 0:
            continue
        wk = age // 7
        if 0 <= wk < n_weeks:
            series[wk] += _load(f)
    return series


def _trailing_4wk(series: List[float], i: int) -> float:
    """Average weekly distance over the 4 weeks starting at index `i` (the 4-week
    block ending `i` weeks ago), averaged over the weeks actually present."""
    block = series[i : i + _SUSTAINED_WEEKS]
    return sum(block) / len(block) if block else 0.0


def _window_rate(facts: List[Any], as_of: date, lo: int, hi: int, first_age: int) -> Optional[float]:
    """Average weekly distance over the age window [lo, hi) days, clamped to history,
    or None when the window is entirely before the runner's first activity."""
    covered_hi = min(hi, first_age + 1)
    span_days = covered_hi - lo
    if span_days < 7:
        return None
    total = sum(_distance(f) for f in facts if lo <= _age_days(f, as_of) < hi)
    return total / (span_days / 7.0)


def _direction(pct: Optional[float]) -> str:
    if pct is None:
        return "no_norm"
    if pct > _TRAJECTORY_DEADBAND_PCT:
        return "up"
    if pct < -_TRAJECTORY_DEADBAND_PCT:
        return "down"
    return "in_line"


def _trajectory(facts: List[Any], as_of: date, first_age: int) -> Tuple[str, Optional[float]]:
    """Multi-year trajectory: the recent 12 months' weekly rate vs the prior 12
    months'. `no_norm` until ~18 months of history exists to resolve a prior year."""
    # Require the prior-year window to hold enough real history before calling a
    # year-over-year trend (else a ~1-year runner gets a "trend" off a two-week sliver).
    prior_covered_days = min(730, first_age + 1) - 365
    if prior_covered_days < _MIN_TRAJECTORY_PRIOR_DAYS:
        return "no_norm", None
    recent = _window_rate(facts, as_of, 0, 365, first_age)
    prior = _window_rate(facts, as_of, 365, 730, first_age)
    if recent is None or prior is None or prior <= 0:
        return "no_norm", None
    pct = round((recent - prior) / prior * 100.0, 1)
    return _direction(pct), pct


def _time_at_current_load_years(series: List[float], current: float) -> Optional[float]:
    """How far back the trailing-4-week weekly rate stayed within a band of the
    runner's CURRENT rate — the "how long have you held this load" durability read.

    Uses the trailing-4-week average (not single weeks) so one easy/rest week does
    not reset it; a genuine multi-week break in load does. None when there is no
    current load to compare against."""
    if current <= 0:
        return None
    lo, hi = current * (1 - _LOAD_BAND_PCT / 100.0), current * (1 + _LOAD_BAND_PCT / 100.0)
    weeks_held = 0
    for i in range(len(series)):
        if lo <= _trailing_4wk(series, i) <= hi:
            weeks_held += 1
        else:
            break
    return round(weeks_held * 7 / 365.25, 1)


def build_training_history(
    facts: List[Any], as_of: date, *, recent_weeks_bound: bool = False
) -> Optional[TrainingHistoryContext]:
    """Build the multi-year training-history picture as of `as_of`, or None when the
    runner has too little history beyond the recent window to describe (the section
    is then dropped from serialization, byte-stable). Facts are duck-typed
    `ActivityFact`s: each needs `local_date`, `activity_type`, `distance_m` (and, on the
    enriched path, `effort_score`).

    Expects facts spanning the runner's full available history (the caller fetches a
    wide window); buckets and traits self-limit to what is present.

    `recent_weeks_bound` (ADR 0026 Slice 2 / grouped_v2, #670): begin the ladder at 14 days
    (after the day-resolved `recent_weeks` window) instead of 60, and enrich each bucket
    with a modality-agnostic weekly LOAD rate, a per-type split, and calendar bounds, plus
    two LOAD durability traits. Default False reproduces the original 60d ladder
    BYTE-IDENTICALLY (the enrichment fields stay None and are dropped), so every prior
    prompt is untouched."""
    dated = [f for f in facts if _age_days(f, as_of) >= 0]
    if len(dated) < _MIN_ACTIVITIES:
        return None
    first_date = min(f.local_date for f in dated)
    first_age = (as_of - first_date).days
    if first_age < _MIN_HISTORY_DAYS:
        return None

    n_weeks = min(first_age // 7 + 1, _MAX_WEEKS)
    series = _weekly_distance_back(dated, as_of, n_weeks)

    # Peak sustained: highest rolling 4-week average weekly distance over all history.
    peak = max((_trailing_4wk(series, i) for i in range(len(series))), default=0.0)
    current = _trailing_4wk(series, 0)
    trajectory_dir, trajectory_pct = _trajectory(dated, as_of, first_age)

    # grouped_v2: the modality-agnostic LOAD durability read (effort_score peak + vs-peak),
    # so a ski/bike block still reads as sustained training. None on the original ladder.
    peak_load: Optional[int] = None
    current_vs_peak_load_pct: Optional[float] = None
    if recent_weeks_bound:
        load_series = _weekly_load_back(dated, as_of, n_weeks)
        peak_load_rate = max(
            (_trailing_4wk(load_series, i) for i in range(len(load_series))), default=0.0
        )
        current_load = _trailing_4wk(load_series, 0)
        peak_load = int(peak_load_rate)
        current_vs_peak_load_pct = (
            round(current_load / peak_load_rate * 100.0, 1) if peak_load_rate > 0 else None
        )

    traits = TrainingHistoryTraits(
        training_age_years=round(first_age / 365.25, 1),
        peak_sustained_weekly_distance_m=int(peak),
        # current weekly volume is NOT restated here (it lives in recent_training.last_30d,
        # #451 one-lane); only its RELATIONSHIP to the peak rides this section.
        current_vs_peak_pct=round(current / peak * 100.0, 1) if peak > 0 else None,
        trajectory_direction=trajectory_dir,
        trajectory_pct=trajectory_pct,
        time_at_current_load_years=_time_at_current_load_years(series, current),
        peak_sustained_weekly_load=peak_load,
        current_vs_peak_load_pct=current_vs_peak_load_pct,
    )

    ladder = _BUCKETS_2WK if recent_weeks_bound else _BUCKETS
    timeline = [
        b
        for (start, end, label) in ladder
        if (
            b := _bucket(
                dated, as_of, first_age, start, end, label, enriched=recent_weeks_bound
            )
        )
        is not None
    ]

    return TrainingHistoryContext(traits=traits, timeline=timeline)
