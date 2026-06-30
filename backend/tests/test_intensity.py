"""#578: the deterministic intensity-distribution-and-trend builder.

Ground truth is constructed from the documented band collapse (easy = Z1-Z2 /
recovery+easy, moderate = Z3 / moderate, hard = Z4-Z5 / tempo+hard) and the explicit
thresholds in `intensity.py` (28-day windows, >=4 comparable sessions, the trend and
vs-recent deadbands). Each expected value is derived from those rules, not invented:
a window of N sessions with K hard ones has a hard share of K/N, and the confounder
exculpation reclassifies a flagged hard/moderate session to easy.
"""

from datetime import date, timedelta

from app.services.coach.intensity import (
    _MIN_SESSIONS,
    _WINDOW_DAYS,
    build_intensity,
)

AS_OF = date(2026, 6, 28)


class _Fact:
    """A duck-typed ActivityFact: the builder reads activity_id/local_date/effort/
    time_in_zones/user_intent."""

    def __init__(
        self,
        activity_id,
        days_ago,
        *,
        effort=None,
        time_in_zones=None,
        user_intent=None,
    ):
        self.activity_id = activity_id
        self.local_date = AS_OF - timedelta(days=days_ago)
        self.effort = effort
        self.time_in_zones = time_in_zones
        self.user_intent = user_intent


def _window(effort, *, start_id, days_base, n):
    """`n` comparable sessions of one effort band, spaced ~3 days apart starting at
    `days_base` days ago."""
    return [
        _Fact(start_id + i, days_base + i * 3, effort=effort) for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# This-session read: band collapse + within-run share                         #
# --------------------------------------------------------------------------- #
def test_this_session_band_collapses_from_effort():
    this = _Fact("this", 0, effort="tempo")  # tempo -> hard
    ctx = build_intensity([this], set(), "this", AS_OF)
    assert ctx is not None
    assert ctx.this_session.band == "hard"
    assert ctx.this_session.hr_confounded is False


def test_this_session_within_run_share_from_zones():
    # 600s easy (Z1+Z2), 200s moderate (Z3), 200s hard (Z4+Z5) -> 60/20/20.
    tiz = {"Z1": 400, "Z2": 200, "Z3": 200, "Z4": 150, "Z5": 50}
    this = _Fact("this", 0, effort="easy", time_in_zones=tiz)
    ctx = build_intensity([this], set(), "this", AS_OF)
    wr = ctx.this_session.within_run
    assert wr is not None
    assert wr.easy_pct == 60.0
    assert wr.moderate_pct == 20.0
    assert wr.hard_pct == 20.0


def test_this_session_without_hr_has_no_band():
    this = _Fact("this", 0, effort=None)
    # No comparable sessions either -> nothing to say -> None.
    assert build_intensity([this], set(), "this", AS_OF) is None


# --------------------------------------------------------------------------- #
# Recent distribution: session-count share                                    #
# --------------------------------------------------------------------------- #
def test_distribution_is_session_count_share():
    # Recent window: 6 easy + 2 hard = 8 sessions -> 75% easy / 0% mod / 25% hard.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=2, n=6)
    facts += _window("hard", start_id=100, days_base=3, n=2)
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.has_distribution is True
    assert ctx.session_count == 8
    assert ctx.distribution.easy_pct == 75.0
    assert ctx.distribution.hard_pct == 25.0
    assert ctx.distribution.moderate_pct == 0.0


def test_this_run_vs_recent_harder_than_mostly_easy_window():
    # A hard run against an all-easy recent window reads as harder.
    facts = [_Fact("this", 0, effort="hard")]
    facts += _window("easy", start_id=10, days_base=2, n=6)
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.this_run_vs_recent == "harder"


def test_this_run_vs_recent_in_line():
    # An easy run against an all-easy window reads as in_line.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=2, n=6)
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.this_run_vs_recent == "in_line"


# --------------------------------------------------------------------------- #
# Confounder exculpation                                                       #
# --------------------------------------------------------------------------- #
def test_confounded_hard_session_is_exculpated_to_easy():
    # 4 easy + 4 hard, but all 4 hard sessions are heat/hill confounded -> the adjusted
    # distribution reads 100% easy while the raw distribution keeps 50% hard.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=2, n=4)
    hard = _window("hard", start_id=100, days_base=3, n=4)
    facts += hard
    confounded = {f.activity_id for f in hard}
    ctx = build_intensity(facts, confounded, "this", AS_OF)
    assert ctx.distribution.hard_pct == 50.0
    assert ctx.distribution_adjusted.hard_pct == 0.0
    assert ctx.distribution_adjusted.easy_pct == 100.0
    assert ctx.confounded_session_count == 4


def test_this_session_confounded_flag_set():
    this = _Fact("this", 0, effort="hard")
    ctx = build_intensity([this] + _window("easy", start_id=10, days_base=2, n=4),
                          {"this"}, "this", AS_OF)
    assert ctx.this_session.hr_confounded is True


# --------------------------------------------------------------------------- #
# Trend: recent vs prior equal window (exculpated hard-share)                  #
# --------------------------------------------------------------------------- #
def test_trend_harder_when_recent_hard_share_rises():
    # Recent 28d: 2 easy + 4 hard (66.7% hard). Prior 28d: 6 easy (0% hard). -> harder.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=1, n=2)
    facts += _window("hard", start_id=20, days_base=7, n=4)
    facts += _window("easy", start_id=200, days_base=_WINDOW_DAYS + 1, n=6)
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.prior_session_count == 6
    assert ctx.trend_direction == "harder"
    assert ctx.trend_hard_share_delta_pct is not None
    assert ctx.trend_hard_share_delta_pct > 10.0


def test_trend_abstains_when_prior_window_thin():
    # Recent window is rich, prior window has too few sessions -> trend no_norm.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=1, n=5)
    facts += _window("hard", start_id=200, days_base=_WINDOW_DAYS + 1, n=1)
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.has_distribution is True
    assert ctx.trend_direction == "no_norm"
    assert ctx.trend_hard_share_delta_pct is None


# --------------------------------------------------------------------------- #
# Abstention on thin history                                                   #
# --------------------------------------------------------------------------- #
def test_thin_window_still_emits_but_abstains_on_distribution():
    # Only this run + 2 comparable sessions (< MIN): section present, no distribution.
    facts = [_Fact("this", 0, effort="moderate")]
    facts += _window("easy", start_id=10, days_base=2, n=_MIN_SESSIONS - 2)
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx is not None
    assert ctx.this_session.band == "moderate"
    assert ctx.has_distribution is False
    assert ctx.distribution is None
    assert ctx.this_run_vs_recent == "no_norm"
    assert ctx.trend_direction == "no_norm"


def test_no_band_and_no_comparable_returns_none():
    # This run has no HR and nothing comparable -> nothing to say -> dropped.
    assert build_intensity([_Fact("this", 0, effort=None)], set(), "this", AS_OF) is None


def test_races_excluded_from_distribution():
    # A declared race in the window does not count toward the distribution.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=2, n=4)
    facts.append(_Fact("race", 5, effort="hard", user_intent="race"))
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.session_count == 4  # the race is excluded
    assert ctx.distribution.hard_pct == 0.0


def test_window_boundary_excludes_older_sessions():
    # A hard session just OUTSIDE the recent window must not enter the distribution.
    facts = [_Fact("this", 0, effort="easy")]
    facts += _window("easy", start_id=10, days_base=1, n=4)
    facts.append(_Fact("old", _WINDOW_DAYS + 0, effort="hard"))  # 1 day past the window edge
    ctx = build_intensity(facts, set(), "this", AS_OF)
    assert ctx.session_count == 4
    assert ctx.distribution.hard_pct == 0.0
