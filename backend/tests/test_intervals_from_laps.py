"""Tests for interval structure derived from the runner's recorded Strava laps (#170).

Lap field schema (distance, elapsed_time, moving_time, average_speed, average_heartrate,
max_heartrate, lap_index, ...) is the real Strava `/activities/{id}` payload shape
(trust level 2: captured 2026-06-09 from the local production snapshot,
strava_activity_id 18823360273). The interval *arrangement* assembled below (warmup +
uniform reps + recoveries) is synthetic test setup reproducing the #170 bug scenario
(a real warmup + 10x200m session whose laps were ignored and whose stream re-derivation
fabricated oversized reps and dropped the warmup).
"""

from app.services.analysis.intervals import (
    _lap_speed,
    _split_laps_work_rest,
    detect_intervals_from_laps,
)


def _standing_rest_lap(lap_index: int, elapsed: int = 60):
    """A standing/autopaused recovery lap: zero speed, zero distance, real time.

    Cannot use _lap() because it derives elapsed from distance/speed (0/0).
    """
    return {
        "lap_index": lap_index,
        "split": lap_index,
        "name": f"Lap {lap_index}",
        "distance": 0.0,
        "elapsed_time": elapsed,
        "moving_time": 0,
        "average_speed": 0.0,
        "average_heartrate": 150.0,
        "max_heartrate": 160.0,
        "pace_zone": 0,
        "resource_state": 2,
    }


def _lap(
    lap_index: int,
    distance: float,
    average_speed: float,
    *,
    average_heartrate: float | None = None,
    max_heartrate: float | None = None,
    moving_time: int | None = None,
):
    """One Strava lap object (real field shape). elapsed_time derived from distance/speed."""
    elapsed = int(round(distance / average_speed))
    lap = {
        "lap_index": lap_index,
        "split": lap_index,
        "name": f"Lap {lap_index}",
        "distance": round(float(distance), 1),
        "elapsed_time": elapsed,
        "moving_time": int(moving_time if moving_time is not None else elapsed),
        "average_speed": round(float(average_speed), 2),
        "pace_zone": 2,
        "resource_state": 2,
    }
    if average_heartrate is not None:
        lap["average_heartrate"] = round(float(average_heartrate), 1)
    if max_heartrate is not None:
        lap["max_heartrate"] = round(float(max_heartrate), 1)
    return lap


def _make_interval_laps(
    reps: int = 4,
    work_distance: float = 200.0,
    work_speed: float = 4.0,
    rest_distance: float = 100.0,
    rest_speed: float = 2.0,
    warmup_distance: float | None = 1500.0,
    warmup_speed: float = 3.0,
    cooldown_distance: float | None = 800.0,
    cooldown_speed: float = 2.5,
    include_hr: bool = True,
    include_rests: bool = True,
):
    """Assemble a lap array for an interval session: [warmup, (work, rest)..., cooldown]."""
    laps = []
    idx = 1

    def hr(avg, peak):
        return (avg, peak) if include_hr else (None, None)

    if warmup_distance is not None:
        a, p = hr(130.0, 145.0)
        laps.append(_lap(idx, warmup_distance, warmup_speed, average_heartrate=a, max_heartrate=p))
        idx += 1

    for i in range(reps):
        a, p = hr(175.0, 185.0)
        laps.append(_lap(idx, work_distance, work_speed, average_heartrate=a, max_heartrate=p))
        idx += 1
        if include_rests and i < reps - 1:
            a, p = hr(150.0, 160.0)
            laps.append(_lap(idx, rest_distance, rest_speed, average_heartrate=a, max_heartrate=p))
            idx += 1

    if cooldown_distance is not None:
        a, p = hr(125.0, 135.0)
        laps.append(_lap(idx, cooldown_distance, cooldown_speed, average_heartrate=a, max_heartrate=p))
        idx += 1

    return laps


class TestDetectIntervalsFromLaps:
    def test_returns_none_without_laps(self):
        assert detect_intervals_from_laps(None) is None
        assert detect_intervals_from_laps({}) is None
        assert detect_intervals_from_laps({"laps": []}) is None

    def test_returns_none_for_single_whole_activity_lap(self):
        raw = {"laps": [_lap(1, 8000.0, 3.33, average_heartrate=150.0, max_heartrate=165.0)]}
        assert detect_intervals_from_laps(raw) is None

    def test_returns_none_for_uniform_auto_distance_laps(self):
        """8 uniform ~1km auto-laps (a steady run) have no work/rest separation."""
        laps = [
            _lap(i + 1, 1000.0, 3.33, average_heartrate=150.0, max_heartrate=160.0)
            for i in range(8)
        ]
        assert detect_intervals_from_laps({"laps": laps}) is None

    def test_returns_none_when_fewer_than_two_work_laps(self):
        laps = _make_interval_laps(reps=1)
        assert detect_intervals_from_laps({"laps": laps}) is None

    def test_detects_uniform_reps_with_warmup(self):
        """The #170 scenario: warmup + 10 uniform 200m reps + recoveries."""
        laps = _make_interval_laps(reps=10)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["source"] == "recorded_laps"
        assert result["summary"]["rep_count"] == 10
        # Recorded warmup is present, not reported as absent (AC#2).
        assert result["warmup_duration_s"] is not None
        assert result["warmup_duration_s"] > 0
        # Uniform reps -> low variability, high consistency (AC#3).
        assert result["summary"]["work_duration_cv"] is not None
        assert result["summary"]["work_duration_cv"] < 10.0
        assert result["summary"]["consistency_score"] == "high"

    def test_short_recorded_warmup_not_dropped(self):
        """A recorded warmup under the stream heuristic's 120s floor is still reported."""
        # warmup 180m @ 3.0 m/s = 60s, below the stream path's 120s warmup floor.
        laps = _make_interval_laps(reps=4, warmup_distance=180.0, warmup_speed=3.0)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["warmup_duration_s"] == 60

    def test_short_reps_not_dropped(self):
        """Recorded reps shorter than the stream heuristic's 30s floor are kept."""
        # 100m @ 4.0 m/s = 25s reps; the stream path would discard these.
        laps = _make_interval_laps(reps=6, work_distance=100.0, work_speed=4.0)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 6

    def test_work_segment_fields(self):
        laps = _make_interval_laps(reps=4)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        work = result["work_segments"][0]
        for key in (
            "segment_number", "start_time_s", "duration_s",
            "distance_m", "avg_speed_mps", "avg_hr", "peak_hr",
        ):
            assert key in work, f"missing work_segment field {key}"

    def test_rest_segment_fields_and_positive_recovery(self):
        laps = _make_interval_laps(reps=4)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["rest_segments"]
        rest = result["rest_segments"][0]
        for key in ("segment_number", "duration_s", "avg_hr", "hr_recovery_bpm"):
            assert key in rest, f"missing rest_segment field {key}"
        # work peak 185 - rest avg 150 = 35 bpm recovered.
        assert rest["hr_recovery_bpm"] > 0

    def test_rep_distance_reflects_recorded_laps(self):
        """Rep distance comes from the recorded lap, not a stream re-derivation."""
        laps = _make_interval_laps(reps=4, work_distance=200.0)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        for work in result["work_segments"]:
            assert abs(work["distance_m"] - 200.0) < 1.0

    def test_cooldown_detected(self):
        laps = _make_interval_laps(reps=4, cooldown_distance=800.0, cooldown_speed=2.5)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["cooldown_duration_s"] is not None
        assert result["cooldown_duration_s"] > 0

    def test_no_hr_laps_still_works(self):
        laps = _make_interval_laps(reps=4, include_hr=False)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        work = result["work_segments"][0]
        assert work["avg_hr"] is None
        assert work["peak_hr"] is None

    def test_summary_fields_complete(self):
        """Lap-derived structure carries the same summary contract as the stream path."""
        laps = _make_interval_laps(reps=5)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        expected_keys = {
            "total_work_time_s", "total_rest_time_s", "work_to_rest_ratio",
            "rep_count", "avg_work_duration_s", "work_duration_cv",
            "avg_work_speed_mps", "work_speed_cv", "avg_rest_duration_s",
            "avg_hr_recovery_bpm", "consistency_score",
        }
        assert set(result["summary"].keys()) == expected_keys

    def test_top_level_contract_matches_stream_shape(self):
        laps = _make_interval_laps(reps=4)
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        for key in (
            "warmup_duration_s", "cooldown_duration_s",
            "work_segments", "rest_segments", "summary",
        ):
            assert key in result, f"missing top-level field {key}"


class TestLapDetectionEdgeCases:
    """Regression coverage for the #170 adversarial-review findings: continuous
    runs that contain a fast block but no recovery laps, and session shapes
    (cut-down, faded rep, tempo rep) that a warmup-trim heuristic would have
    misfiled -- the laps are reported as the runner marked them."""

    def test_negative_split_run_not_detected(self):
        # Monotonically rising lap speeds, no recovery laps between -> continuous.
        laps = [
            _lap(i + 1, 1000, sp, average_heartrate=150.0, max_heartrate=160.0)
            for i, sp in enumerate([2.8, 2.9, 3.0, 4.0, 4.1, 4.2])
        ]
        assert detect_intervals_from_laps({"laps": laps}) is None

    def test_fast_finish_run_not_detected(self):
        # A steady run with a fast finish: fast laps are contiguous, no recovery.
        laps = [
            _lap(i + 1, 1000, sp, average_heartrate=150.0, max_heartrate=160.0)
            for i, sp in enumerate([3.0, 3.02, 3.05, 4.0, 4.1])
        ]
        assert detect_intervals_from_laps({"laps": laps}) is None

    def test_long_first_rep_at_work_pace_is_kept(self):
        # A long FIRST rep run at work pace (not slower) is a real rep, not a
        # warmup -- it counts as a rep.
        laps = [
            _lap(1, 1600, 4.5, average_heartrate=178.0, max_heartrate=188.0),  # long rep at work pace
            _lap(2, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(3, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(4, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(5, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 3
        assert result["warmup_duration_s"] is None

    def test_faded_final_rep_at_work_pace_is_kept(self):
        # A final rep that fades (slower + longer) is a real rep, not a
        # cooldown -- it must stay in the work set. Classification is by speed:
        # the 3.9 rep stays in the fast cluster because the 2.0->3.9 gap (1.9)
        # is the largest, beating 3.9->4.5 (0.6). HR is display-only, not read
        # for work/rest classification.
        laps = [
            _lap(1, 400, 4.5, average_heartrate=176.0, max_heartrate=185.0),
            _lap(2, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(3, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(4, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(5, 400, 4.5, average_heartrate=179.0, max_heartrate=187.0),
            _lap(6, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(7, 700, 3.9, average_heartrate=180.0, max_heartrate=188.0),  # faded but still fast
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 4
        assert result["cooldown_duration_s"] is None

    def test_long_slow_opening_rep_at_work_pace_is_kept(self):
        # A mixed session (long tempo rep + short fast reps): the opening tempo
        # rep is slower than the short reps but still sits in the fast speed
        # cluster (4.0 vs recovery 2.0), so it is a real rep, not a warmup.
        # Classification is speed-only; HR values are display-only.
        laps = [
            _lap(1, 2000, 4.0, average_heartrate=175.0, max_heartrate=183.0),  # tempo opening rep
            _lap(2, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(3, 400, 5.0, average_heartrate=180.0, max_heartrate=188.0),
            _lap(4, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(5, 400, 5.0, average_heartrate=180.0, max_heartrate=188.0),
            _lap(6, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(7, 400, 5.0, average_heartrate=180.0, max_heartrate=188.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 4
        assert result["warmup_duration_s"] is None

    def test_cutdown_progression_keeps_all_reps(self):
        # A monotone cut-down (each rep faster + higher HR): the slow first rep is
        # a real rep (the end of a smooth ramp), not an outlier warmup, so it must
        # count as a rep.
        specs = [(3.6, 158, 166), (3.9, 168, 176), (4.2, 176, 184),
                 (4.6, 184, 191), (5.0, 190, 196)]
        laps = []
        idx = 1
        for i, (sp, ahr, mhr) in enumerate(specs):
            laps.append(_lap(idx, 800, sp, average_heartrate=ahr, max_heartrate=mhr))
            idx += 1
            if i < len(specs) - 1:
                laps.append(_lap(idx, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0))
                idx += 1
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 5
        assert result["warmup_duration_s"] is None


class TestKnownLimitationBriskWarmup:
    """Characterisation of the documented #170 limitation, NOT desired behaviour.

    A warmup/cooldown lap paced close to rep pace (between recovery and work
    pace) lands in the work cluster and is counted as a rep. A trim heuristic
    that tried to separate it from a real rep by lap stats (speed/HR ratios vs
    the interior reps) was removed: it was under-determined and misfiled
    legitimate session shapes (faded final rep, long tempo rep, cut-down,
    pyramid) in four successive adversarial reviews. These tests pin the
    current behaviour so a future fix (e.g. HR-primary classification or lap
    start_index/end_index cross-check against the stream) shows up as an
    intentional diff. An easy-paced warmup/cooldown is detected correctly
    (see test_detects_uniform_reps_with_warmup)."""

    def test_brisk_warmup_is_counted_as_a_rep(self):
        # Warmup pace (3.3) sits between rest (2.0) and work (4.5): the speed
        # split files it into the work cluster, so it is reported as a rep and
        # the recorded warmup is reported as absent.
        laps = [
            _lap(1, 2000, 3.3, average_heartrate=140.0, max_heartrate=150.0),  # brisk warmup
            _lap(2, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(3, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(4, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(5, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(6, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(7, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 4  # 3 real reps + the brisk warmup
        assert result["warmup_duration_s"] is None

    def test_brisk_cooldown_is_counted_as_a_rep(self):
        laps = [
            _lap(1, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(2, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(3, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(4, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(5, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(6, 1800, 3.3, average_heartrate=140.0, max_heartrate=150.0),  # brisk cooldown
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 4  # 3 real reps + the brisk cooldown
        assert result["cooldown_duration_s"] is None

    def test_two_reps_bracketed_by_brisk_warmup_and_cooldown(self):
        laps = [
            _lap(1, 2000, 3.3, average_heartrate=140.0, max_heartrate=150.0),  # brisk warmup
            _lap(2, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(3, 200, 2.0, average_heartrate=150.0, max_heartrate=160.0),
            _lap(4, 400, 4.5, average_heartrate=178.0, max_heartrate=186.0),
            _lap(5, 1800, 3.3, average_heartrate=140.0, max_heartrate=150.0),  # brisk cooldown
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["summary"]["rep_count"] == 4  # 2 real reps + warmup + cooldown
        assert result["warmup_duration_s"] is None
        assert result["cooldown_duration_s"] is None


class TestStandingRestRecoveries:
    """A track session with standing (zero-speed) recoveries is the canonical
    recorded-laps interval session. A zero-speed rest lap is the strongest
    work/rest signal, not unusable data, so it must NOT abort the lap path
    (#170 adversarial-review finding F2)."""

    def test_standing_rest_session_is_detected(self):
        # 3 reps separated by standing recoveries (runner stood still, lapped).
        laps = [
            _lap(1, 400, 5.0, average_heartrate=178.0, max_heartrate=186.0),
            _standing_rest_lap(2),
            _lap(3, 400, 5.0, average_heartrate=179.0, max_heartrate=187.0),
            _standing_rest_lap(4),
            _lap(5, 400, 5.0, average_heartrate=180.0, max_heartrate=188.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})

        assert result is not None
        assert result["source"] == "recorded_laps"
        assert result["summary"]["rep_count"] == 3
        # Both standing recoveries are read as rest segments.
        assert len(result["rest_segments"]) == 2
        # Recorded standing-rest duration is preserved (60s each).
        assert result["rest_segments"][0]["duration_s"] == 60

    def test_single_zero_speed_lap_does_not_abort_detection(self):
        # Regression: one zero-speed recovery used to return None for the WHOLE
        # session (the all-or-nothing speed guard), falling back to streams.
        laps = [
            _lap(1, 400, 5.0),
            _standing_rest_lap(2),
            _lap(3, 400, 5.0),
            _lap(4, 200, 2.0),
            _lap(5, 400, 5.0),
        ]
        assert detect_intervals_from_laps({"laps": laps}) is not None


class TestLapSpeedAndSeparationGuard:
    """Direct coverage for the changed lap helpers (#170 finding F5):
    _lap_speed's fallbacks and the _MIN_CLUSTER_SEPARATION rejection that no
    test previously pinned (it survived mutation to 1.0)."""

    def test_lap_speed_reads_recorded_field(self):
        assert _lap_speed({"average_speed": 4.5}) == 4.5

    def test_lap_speed_falls_back_to_distance_over_time(self):
        # No average_speed: compute from distance/elapsed_time.
        assert _lap_speed({"distance": 400.0, "elapsed_time": 100}) == 4.0

    def test_lap_speed_falls_back_to_moving_time(self):
        # No average_speed and no elapsed_time: use moving_time.
        assert _lap_speed({"distance": 400.0, "moving_time": 80}) == 5.0

    def test_lap_speed_zero_is_data_not_missing(self):
        # A genuinely-recorded standing rest is 0.0, not None.
        assert _lap_speed({"average_speed": 0.0, "distance": 0.0, "elapsed_time": 60}) == 0.0

    def test_lap_speed_unreadable_lap_is_none(self):
        assert _lap_speed({}) is None
        assert _lap_speed({"distance": 400.0}) is None  # no time

    def test_separation_guard_rejects_insufficient_ratio(self):
        # fast/slow ratio 3.6/3.0 = 1.2 < 1.3: not a real work/rest split.
        assert _split_laps_work_rest([3.0, 3.0, 3.6, 3.6]) is None

    def test_separation_guard_accepts_clear_ratio(self):
        # fast/slow ratio 4.2/3.0 = 1.4 >= 1.3: a real work/rest split.
        assert _split_laps_work_rest([3.0, 3.0, 4.2, 4.2]) == [False, False, True, True]

    def test_separation_guard_accepts_standing_rest_cluster(self):
        # slow cluster is all standing rest (mean 0): accepted on a positive
        # fast cluster alone, the ratio gate does not apply.
        assert _split_laps_work_rest([0.0, 0.0, 5.0, 5.0]) == [False, False, True, True]


class TestAmbiguousSplitAbstain:
    """Guards added by #189: the single-largest-gap split is under-determined
    for two documented failure shapes. The fix abstains (returns None from
    detect_intervals_from_laps) rather than asserting a high-confidence
    mis-segmentation as ground truth.

    The existing TestKnownLimitationBriskWarmup tests (brisk warmup/cooldown
    adjacent to reps) are UNAFFECTED because those consecutive-work pairs occur
    only at the edge of the work window, not in the interior (#188 covers that
    root cause separately).
    """

    # ------------------------------------------------------------------
    # Shape 1: heterogeneous recoveries (#189 finding F6)
    # One walked recovery (~1.2 m/s), one jogged recovery (~3.6 m/s).
    # The largest gap falls BETWEEN the two recovery speeds rather than
    # between all recoveries and the work laps, so the jogged recovery is
    # promoted into the work cluster. The result: rep count off by one,
    # two adjacent work segments, inflated work-speed CV.
    # ------------------------------------------------------------------

    def test_heterogeneous_recoveries_abstains(self):
        """Walk + jog recoveries: the interior jog-rest lap must not be
        classified as a work rep. The lap split is under-determined and
        should abstain rather than emit a confident mis-segmentation.
        """
        laps = [
            _lap(1, 400, 5.0, average_heartrate=178.0, max_heartrate=188.0),
            _lap(2, 200, 1.2, average_heartrate=135.0, max_heartrate=145.0),  # walk recovery
            _lap(3, 400, 5.0, average_heartrate=180.0, max_heartrate=190.0),
            _lap(4, 200, 3.6, average_heartrate=155.0, max_heartrate=165.0),  # jog recovery
            _lap(5, 400, 5.0, average_heartrate=179.0, max_heartrate=189.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})
        assert result is None, (
            "heterogeneous recoveries (walk 1.2 m/s + jog 3.6 m/s) should abstain; "
            f"got rep_count={result['summary']['rep_count'] if result else None}"
        )

    def test_heterogeneous_recoveries_larger_session_abstains(self):
        """Same shape with more reps: the ambiguous walk-vs-jog recovery
        classification still causes abstention regardless of total rep count.
        """
        # 4 reps: rest0=walk, rest1=jog, rest2=walk, rest3=jog
        laps = [
            _lap(1, 400, 5.0, average_heartrate=178.0, max_heartrate=188.0),
            _lap(2, 200, 1.2, average_heartrate=133.0, max_heartrate=143.0),  # walk
            _lap(3, 400, 5.0, average_heartrate=180.0, max_heartrate=190.0),
            _lap(4, 200, 3.6, average_heartrate=155.0, max_heartrate=165.0),  # jog
            _lap(5, 400, 5.0, average_heartrate=179.0, max_heartrate=189.0),
            _lap(6, 200, 1.2, average_heartrate=133.0, max_heartrate=143.0),  # walk
            _lap(7, 400, 5.0, average_heartrate=181.0, max_heartrate=191.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})
        assert result is None, (
            "heterogeneous recoveries in a larger session should still abstain"
        )

    # ------------------------------------------------------------------
    # Shape 2: easy run with one mid-run standing stop (#189, residual from F2)
    # A steady easy-pace run where the runner pressed the lap button during
    # a brief standing stop. The standing stop is the sole 'rest' lap;
    # everything else is the same easy running pace.
    # ------------------------------------------------------------------

    def test_easy_run_with_single_standing_stop_abstains(self):
        """A steady easy run with one mid-run standing stop should not be
        detected as a 4-rep interval session. The standing-rest special case
        must not fire when the 'work' cluster is just a uniform steady pace.
        """
        laps = [
            _lap(1, 1000, 3.0, average_heartrate=145.0, max_heartrate=155.0),
            _lap(2, 1000, 3.1, average_heartrate=147.0, max_heartrate=157.0),
            _standing_rest_lap(3),                                              # brief stop
            _lap(4, 1000, 3.0, average_heartrate=145.0, max_heartrate=155.0),
            _lap(5, 1000, 3.1, average_heartrate=147.0, max_heartrate=157.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})
        assert result is None, (
            "easy run with one standing stop should abstain; "
            f"got rep_count={result['summary']['rep_count'] if result else None}"
        )

    def test_standing_stop_mid_faster_easy_run_abstains(self):
        """Slightly faster easy pace (still easy running, not interval work):
        the abstain condition must not depend on any absolute-pace threshold.
        """
        laps = [
            _lap(1, 1000, 3.5, average_heartrate=150.0, max_heartrate=160.0),
            _lap(2, 1000, 3.5, average_heartrate=151.0, max_heartrate=161.0),
            _standing_rest_lap(3),
            _lap(4, 1000, 3.5, average_heartrate=150.0, max_heartrate=160.0),
            _lap(5, 1000, 3.5, average_heartrate=151.0, max_heartrate=161.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})
        assert result is None, (
            "slightly faster easy run with one standing stop should also abstain"
        )

    # ------------------------------------------------------------------
    # Preservation: clean interval sessions must still be detected.
    # ------------------------------------------------------------------

    def test_homogeneous_recoveries_detected(self):
        """When ALL recoveries are at the same pace (uniformly jogged), the
        split is unambiguous and the session must still be detected.
        """
        laps = [
            _lap(1, 400, 5.0, average_heartrate=178.0, max_heartrate=188.0),
            _lap(2, 200, 2.0, average_heartrate=145.0, max_heartrate=155.0),
            _lap(3, 400, 5.0, average_heartrate=180.0, max_heartrate=190.0),
            _lap(4, 200, 2.0, average_heartrate=145.0, max_heartrate=155.0),
            _lap(5, 400, 5.0, average_heartrate=179.0, max_heartrate=189.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})
        assert result is not None
        assert result["summary"]["rep_count"] == 3

    def test_standing_rest_clean_interval_still_detected(self):
        """A genuine track session with standing recoveries AND clear work
        pace must not be abstained by the standing-rest guard.
        The work speeds (5.0 m/s) are well above typical easy-running pace.
        """
        laps = [
            _lap(1, 400, 5.0, average_heartrate=178.0, max_heartrate=186.0),
            _standing_rest_lap(2),
            _lap(3, 400, 5.0, average_heartrate=179.0, max_heartrate=187.0),
            _standing_rest_lap(4),
            _lap(5, 400, 5.0, average_heartrate=180.0, max_heartrate=188.0),
        ]
        result = detect_intervals_from_laps({"laps": laps})
        assert result is not None
        assert result["summary"]["rep_count"] == 3
