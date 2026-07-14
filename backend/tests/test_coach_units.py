"""Contract for the shared coach-facing unit formatters (ADR 0026 Slice 4, #680)."""

import pytest

from app.services.coach import coach_units as u


class TestDistance:
    def test_metres_to_km_one_dp(self):
        assert u.km(4879) == 4.9
        assert u.km(71655) == 71.7
        assert u.km(43150) == 43.1   # round(43.15, 1) == 43.1 (pack-wide Python round)

    def test_none_and_zero(self):
        assert u.km(None) is None
        assert u.km(0) is None


class TestDuration:
    def test_minute_granularity(self):
        assert u.duration(1834) == "30m"        # 30:34 -> 30m (seconds dropped)
        assert u.duration(2400) == "40m"
        assert u.duration(3661) == "1h01m"

    def test_none_and_zero(self):
        assert u.duration(None) is None
        assert u.duration(0) is None


class TestDurationPrecise:
    def test_second_resolution(self):
        assert u.duration_precise(90) == "1:30"
        assert u.duration_precise(12) == "0:12"     # short zone time, never '0m'
        assert u.duration_precise(660) == "11:00"
        assert u.duration_precise(436) == "7:16"

    def test_explicit_hours(self):
        assert u.duration_precise(3661) == "1:01:01"

    def test_none_and_negative(self):
        assert u.duration_precise(None) is None
        assert u.duration_precise(-5) is None


class TestPace:
    def test_from_speed_mps(self):
        # 4.25 m/s -> 1000/4.25 = 235.3 s/km -> 3:55/km
        assert u.pace_from_speed(4.25) == "3:55/km"

    def test_from_distance_and_time(self):
        assert u.pace(8000, 2400) == "5:00/km"

    def test_from_sec_per_km(self):
        assert u.pace_from_sec_per_km(300) == "5:00/km"

    def test_seconds_rounding_carry(self):
        # 359.6 s/km rounds to 6:00, not 5:60
        assert u.pace_from_sec_per_km(359.6) == "6:00/km"

    def test_none_and_zero(self):
        assert u.pace_from_speed(None) is None
        assert u.pace_from_speed(0) is None
        assert u.pace(0, 100) is None
        assert u.pace(100, None) is None


class TestHeartRate:
    def test_bpm_with_pct_max(self):
        assert u.hr_bpm(165.6, 191) == "166 bpm (87% max)"
        assert u.hr_bpm(186.0, 191) == "186 bpm (97% max)"

    def test_bpm_without_max_falls_back_to_plain(self):
        assert u.hr_bpm(165.6, None) == "166 bpm"
        assert u.hr_bpm(165.6, 0) == "166 bpm"

    def test_plain_bpm(self):
        assert u.bpm(163.9) == 164
        assert u.bpm(None) is None

    def test_pct_of_max(self):
        assert u.pct_of_max(165.6, 191) == 87
        assert u.pct_of_max(165.6, None) is None

    def test_hr_none(self):
        assert u.hr_bpm(None, 191) is None


class TestTrim:
    def test_drops_trailing_zero(self):
        assert u.trim(101.0) == 101
        assert u.trim(30.0) == 30
        assert isinstance(u.trim(30.0), int)

    def test_keeps_real_fractional(self):
        assert u.trim(1.15) == 1.15
        assert u.trim(4.2) == 4.2

    def test_passes_through_non_numbers(self):
        assert u.trim("easy") == "easy"
        assert u.trim(None) is None
        assert u.trim(True) is True
