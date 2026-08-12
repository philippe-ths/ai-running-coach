from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.activity import ActivityRead
from app.schemas.checkin import CheckInRead
from app.services.units.cadence import normalize_cadence_spm
from app.services.analysis.smoothing import smooth_cadence


class DerivedMetricRead(BaseModel):
    # Classification axes (ADR 0007) + headline injected by the read endpoint.
    headline: Optional[str] = None
    effort: Optional[str] = None
    duration_class: Optional[str] = None
    structure: Optional[str] = None
    is_hilly: Optional[bool] = None
    is_race: Optional[bool] = None
    effort_score: float
    pace_variability: Optional[float] = None
    hr_drift: Optional[float] = None
    flags: List[str] = []
    confidence: str
    confidence_reasons: List[str] = []
    time_in_zones: Optional[Dict] = None
    # Source used to bin time_in_zones: "strava" when the runner's own Strava HR
    # zone bounds are stored on UserProfile.hr_zones, else None (%-of-max-HR
    # fallback). Injected by the detail endpoint from UserProfile; never stored
    # on DerivedMetric itself (#301).
    hr_zones_source: Optional[str] = None
    stops_analysis: Optional[Dict] = None
    efficiency_analysis: Optional[Dict] = None
    model_config = ConfigDict(from_attributes=True)


class ActivityStreamRead(BaseModel):
    stream_type: str
    data: List[Any]
    model_config = ConfigDict(from_attributes=True)


class SplitRead(BaseModel):
    split: int
    split_type: str = "distance"
    distance: Optional[float] = None
    elapsed_time: float
    pace: Optional[float] = None
    speed: Optional[float] = None
    avg_hr: Optional[float] = None
    avg_grade: Optional[float] = None
    avg_cadence: Optional[float] = None
    avg_watts: Optional[float] = None
    elev_gain: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


class LapRead(BaseModel):
    """One recorded Strava lap, projected from raw_summary at read time (#208)."""

    lap: int
    name: Optional[str] = None
    distance_m: Optional[float] = None
    elapsed_time_s: Optional[int] = None
    moving_time_s: Optional[int] = None
    avg_speed_mps: Optional[float] = None
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    avg_cadence: Optional[float] = None


class TrainingLoadRead(BaseModel):
    """The P3 readiness read for this activity, projected at read time from the user's
    windowed history (ADR 0016). None when there is no history to compute it from.
    `fitness`/`fatigue`/`form` are in Edwards zone-minutes; `warming_up` means the
    chronic baseline is not yet established and the condition is provisional."""

    fitness: float
    fatigue: float
    form: float
    ramp_rate: float
    condition: str
    trend: str
    ramp_aggressive: bool
    warming_up: bool
    sample_count: int


class ActivityDetailRead(ActivityRead):
    # The full Strava summary JSON, carried only on the per-activity detail view
    # (the home list omits it, #359). The detail page renders elapsed_time,
    # max_heartrate, suffer_score, power, and device fields from here.
    raw_summary: Dict[str, Any] = {}
    metrics: Optional[DerivedMetricRead] = None
    check_in: Optional[CheckInRead] = None
    streams: List[ActivityStreamRead] = []
    splits: List[SplitRead] = []
    # Recorded Strava laps (the runner's own lap-button marks). Empty when the
    # activity has no usable recorded laps (#208).
    laps: List[LapRead] = []
    # P3 readiness (ADR 0016): the runner's current-condition read as of this
    # activity, projected at read time (the `laps` idiom). None when there is no
    # history. Shown to the runner regardless of the active coach prompt — the model
    # is always computable; the COACH_PROMPT_ID gate governs only the coach surface.
    training_load: Optional[TrainingLoadRead] = None
    # The stated-intent labels this activity's picker offers, injected at read
    # time from `services.intents` (the `laps`/`training_load` idiom, #779). The
    # frontend renders the picker from this rather than holding its own copy of
    # the vocabulary, so the coach's allowed labels and the runner's selectable
    # ones cannot drift apart. Carries the activity's own stored intent when that
    # value predates (or falls outside) the current vocabulary.
    intent_options: List[str] = []

    @model_validator(mode="after")
    def normalize_stream_cadence(self) -> "ActivityDetailRead":
        effective_type = self.user_intent if self.user_intent else self.type

        if not self.streams:
            return self

        cadence_stream = next(
            (s for s in self.streams if s.stream_type == "cadence"), None
        )
        if not cadence_stream:
            return self

        test_val = 80.0
        norm_val = normalize_cadence_spm(effective_type, test_val)
        should_normalize = norm_val == 160.0

        if should_normalize:
            nums = [x for x in cadence_stream.data if isinstance(x, (int, float))]
            if not nums:
                return self

            stream_avg = sum(nums) / len(nums)

            if stream_avg < 130:
                cadence_stream.data = [
                    x * 2 if isinstance(x, (int, float)) else x
                    for x in cadence_stream.data
                ]

        return self

    @model_validator(mode="after")
    def generate_smoothed_cadence(self) -> "ActivityDetailRead":
        """
        Generates a 'smoothed_cadence' stream for better visualization.
        """
        if not self.streams:
            return self

        # Helper to get stream data safely
        def get_stream_data(name: str) -> List[Any]:
            s = next((s for s in self.streams if s.stream_type == name), None)
            return s.data if s else []

        cadence = get_stream_data("cadence")
        if not cadence:
            return self

        velocity = get_stream_data("velocity_smooth")
        moving = get_stream_data("moving")
        time = get_stream_data("time")

        # Time is required for gap interpolation
        if not time:
            return self

        # Prevent duplicate generation if validator runs multiple times
        if any(s.stream_type == "smoothed_cadence" for s in self.streams):
           return self

        smoothed_data = smooth_cadence(cadence, velocity, moving, time)

        # Append new stream
        self.streams.append(
            ActivityStreamRead(stream_type="smoothed_cadence", data=smoothed_data)
        )

        # Recompute average from smoothed data (ignoring nulls)
        valid_values = [v for v in smoothed_data if v is not None]
        if valid_values:
            self.avg_cadence = sum(valid_values) / len(valid_values)

        return self
