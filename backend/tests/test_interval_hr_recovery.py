"""Peak-to-trough HR recovery for interval sessions (#636).

`hr_recovery_bpm` used to be `rep_peak - mean(recovery)`, which understated
recovery and collapsed toward zero as recovery jogs got harder even when the
true drop stayed large. It is now `rep_peak - min(recovery)` read from the raw
HR stream.

The lap-based detector selects the recovery window by TIME, not by the lap's
Strava record indices: those indices reference a different stream resolution
than the one stored, so index slicing lands on the wrong samples. These tests
pin the helpers and lock the fix against the real lap-recorded session that
surfaced the bug (Strava activity 19217514225), using its STORED pipeline
streams and laps.
"""

import json
from pathlib import Path

from app.services.analysis.intervals import (
    _hr_recovery_bpm,
    _hr_window_by_time,
    detect_intervals_from_laps,
)
from app.services.analysis.workout_matching import build_interval_kpis

_FIXTURE = Path(__file__).parent / "fixtures" / "interval_recovery_real.json"


class TestHrRecoveryHelper:
    def test_peak_minus_trough(self):
        # HR falls from a 180 peak to a 140 floor during recovery -> 40 bpm.
        assert _hr_recovery_bpm(180, [175, 168, 150, 140, 145]) == 40.0

    def test_uses_trough_not_mean(self):
        window = [175, 170, 160, 140, 150]  # mean 159, min 140
        assert _hr_recovery_bpm(182, window) == round(182 - 140, 1)  # 42, not 182-159=23

    def test_abstains_without_peak_or_window(self):
        assert _hr_recovery_bpm(None, [150, 140]) is None
        assert _hr_recovery_bpm(180, None) is None
        assert _hr_recovery_bpm(180, []) is None

    def test_ignores_non_numeric_entries(self):
        assert _hr_recovery_bpm(180, [None, 150, None, 138]) == 42.0


class TestHrWindowByTime:
    def test_selects_samples_in_elapsed_window(self):
        hr = [150, 160, 145, 140, 138, 170]
        time = [0, 10, 20, 30, 40, 50]
        # [20, 45) -> indices 2,3,4
        assert _hr_window_by_time(hr, time, 20, 45) == [145, 140, 138]

    def test_end_is_exclusive_start_inclusive(self):
        hr = [100, 110, 120, 130]
        time = [0, 10, 20, 30]
        assert _hr_window_by_time(hr, time, 10, 30) == [110, 120]  # excludes t=30

    def test_abstains_without_streams_or_empty_window(self):
        assert _hr_window_by_time(None, [0, 1], 0, 1) is None
        assert _hr_window_by_time([1, 2], None, 0, 1) is None
        assert _hr_window_by_time([100, 110], [0, 10], 500, 600) is None  # no sample in range


class TestRealSessionRecovery:
    """The session the runner flagged: the old metric reported recovery
    'collapsing to 6 bpm'; the runner's HR actually dropped 30-44 bpm each rep.
    """

    def _load(self):
        data = json.loads(_FIXTURE.read_text())
        streams = {"heartrate": data["heartrate"], "time": data["time"]}
        return data["laps"], streams

    def test_recovery_is_peak_to_trough_on_real_data(self):
        laps, streams = self._load()
        result = detect_intervals_from_laps({"laps": laps}, streams)
        assert result is not None
        recoveries = [r["hr_recovery_bpm"] for r in result["rest_segments"]]
        # Ground truth from the stored raw HR stream, windowed by lap elapsed
        # time: rep peak minus the trough reached during each recovery jog.
        assert recoveries == [44.0, 44.0, 36.0, 35.0, 30.0, 34.0]

    def test_fix_departs_from_legacy_peak_minus_mean(self):
        laps, streams = self._load()
        result = detect_intervals_from_laps({"laps": laps}, streams)
        rests = result["rest_segments"]
        reps = result["work_segments"]
        # Reconstruct the retired peak-minus-mean number and confirm the new
        # metric is materially larger (the bug was a ~4-7x understatement).
        for idx, rest in enumerate(rests):
            legacy = reps[idx]["peak_hr"] - rest["avg_hr"]
            assert rest["hr_recovery_bpm"] > legacy + 15

    def test_abstains_when_stream_absent(self):
        laps, _ = self._load()
        result = detect_intervals_from_laps({"laps": laps})  # no streams
        assert result is not None
        assert all(r["hr_recovery_bpm"] is None for r in result["rest_segments"])


class TestRealSessionCoachView:
    """The coach-facing shape (#637): per-rep pace/%max, per-rest restart floor,
    and the two trends, on the same real 7x400m session (max HR 190)."""

    def _run(self):
        data = json.loads(_FIXTURE.read_text())
        streams = {"heartrate": data["heartrate"], "time": data["time"]}
        return detect_intervals_from_laps({"laps": data["laps"]}, streams, max_hr=190)

    def test_per_rep_pace_and_effort(self):
        reps = self._run()["work_segments"]
        # Pace in s/km (3:45, 3:42, ... then the middle-set fade), effort as %max.
        assert [r["pace_s_per_km"] for r in reps] == [225, 222, 230, 248, 242, 245, 238]
        assert [r["peak_hr_pct_max"] for r in reps] == [95, 97, 96, 97, 96, 98, 98]

    def test_per_rest_restart_floor_rises(self):
        rests = self._run()["rest_segments"]
        # The runner restarts each rep progressively hotter -- the fatigue tell.
        assert [r["restart_pct_max"] for r in rests] == [72, 74, 77, 79, 81, 80]

    def test_kpis_read_the_trends(self):
        kpis = build_interval_kpis(self._run(), max_hr=190)
        assert kpis["pace"]["direction"] == "fading"
        assert kpis["pace"]["first_s_per_km"] == 225
        assert kpis["pace"]["last_s_per_km"] == 238
        assert kpis["recovery_floor"]["trend"] == "rising"
        assert kpis["recovery_floor"]["first_pct_max"] == 72
        assert kpis["recovery_floor"]["last_pct_max"] == 80
