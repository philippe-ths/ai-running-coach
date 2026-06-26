"""P1.3 Stance dials — resolution, axes, prompt composition, floor invariance.

Structure mirrors the milestone deliverables and ACs (docs/adr/0015-coaching-stance-is-a-selected-school-plus-two-prompt-steered-emphasis-axes.md):

- TestStanceResolution: stance.py maps a CoachingRelationship row -> StanceProfile
  (null -> default school aerobic-base + balanced 3/3; stored school + emphasis;
  clamping; an undeclared stance is_default). AC5.
- TestStanceAxes: the two operable emphasis axes (Data↔Sentiment, Process↔Outcome)
  are well-formed and orthogonal to the voice dials.

Prompt composition (rule 26 under coach_message_v5) and the cross-stance floor
invariance gate (AC3) live in test_stance.py's composition class and in
test_corpus_invariance.py respectively, added alongside their layers.
"""

from types import SimpleNamespace

from app.services.coach.corpus import DEFAULT_SCHOOL_ID, SCHOOLS
from app.services.coach.stance import (
    DEFAULT_EMPHASIS,
    EMPHASIS_AXES,
    EMPHASIS_MAX,
    EMPHASIS_MIN,
    Emphasis,
    StanceProfile,
    resolve_stance,
)


# A relationship-row stand-in: resolve_stance only reads the stance_* attributes.
def _rel(**stance):
    fields = {
        "stance_school": None,
        "stance_data_sentiment": None,
        "stance_process_outcome": None,
    }
    fields.update(stance)
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestStanceResolution:
    def test_none_resolves_to_default_school_and_balanced(self):
        s = resolve_stance(None)
        assert s.is_default is True
        assert s.school_id == DEFAULT_SCHOOL_ID  # aerobic-base, preserving P1.2
        assert s.emphasis == DEFAULT_EMPHASIS  # balanced 3/3

    def test_empty_row_resolves_to_default(self):
        s = resolve_stance(_rel())
        assert s.is_default is True
        assert s.school_id == DEFAULT_SCHOOL_ID
        assert s.emphasis == DEFAULT_EMPHASIS

    def test_stored_school_resolves_through(self):
        s = resolve_stance(_rel(stance_school="polarized"))
        assert s.is_default is False
        assert s.school_id == "polarized"
        # emphasis falls back to balanced when not declared
        assert s.emphasis == DEFAULT_EMPHASIS

    def test_stored_emphasis_resolves_through(self):
        s = resolve_stance(_rel(stance_data_sentiment=5, stance_process_outcome=1))
        assert s.is_default is False
        # a runner who set only emphasis still gets the default school
        assert s.school_id == DEFAULT_SCHOOL_ID
        assert s.emphasis.data_sentiment == 5
        assert s.emphasis.process_outcome == 1

    def test_partial_emphasis_falls_back_per_axis(self):
        s = resolve_stance(_rel(stance_school="enjoyment-and-consistency", stance_data_sentiment=4))
        assert s.school_id == "enjoyment-and-consistency"
        assert s.emphasis.data_sentiment == 4
        assert s.emphasis.process_outcome == DEFAULT_EMPHASIS.process_outcome

    def test_out_of_range_emphasis_is_clamped(self):
        s = resolve_stance(_rel(stance_data_sentiment=99, stance_process_outcome=-3))
        assert s.emphasis.data_sentiment == EMPHASIS_MAX
        assert s.emphasis.process_outcome == EMPHASIS_MIN

    def test_unknown_stored_school_passes_through_for_seam_to_degrade(self):
        # resolve_stance does not validate the school; fetch_corpus owns the
        # unknown->house-core-only degradation (single source of degradation truth).
        s = resolve_stance(_rel(stance_school="not-a-real-school"))
        assert s.school_id == "not-a-real-school"
        assert s.is_default is False


# ---------------------------------------------------------------------------
# Axes
# ---------------------------------------------------------------------------


class TestStanceAxes:
    def test_two_emphasis_axes_with_expected_poles(self):
        by_key = {a.key: a for a in EMPHASIS_AXES}
        assert set(by_key) == {"data_sentiment", "process_outcome"}
        assert (by_key["data_sentiment"].low_pole, by_key["data_sentiment"].high_pole) == ("Data", "Sentiment")
        assert (by_key["process_outcome"].low_pole, by_key["process_outcome"].high_pole) == ("Process", "Outcome")

    def test_axis_attr_maps_to_a_relationship_column(self):
        for axis in EMPHASIS_AXES:
            assert axis.attr.startswith("stance_")

    def test_default_emphasis_is_balanced_centre(self):
        for _axis, value in DEFAULT_EMPHASIS.as_ordered():
            assert value == 3

    def test_emphasis_as_ordered_pairs_axis_with_value(self):
        e = Emphasis(data_sentiment=2, process_outcome=4)
        ordered = dict((axis.key, value) for axis, value in e.as_ordered())
        assert ordered == {"data_sentiment": 2, "process_outcome": 4}

    def test_default_school_is_a_known_school(self):
        assert DEFAULT_SCHOOL_ID in SCHOOLS

    def test_stance_profile_is_frozen(self):
        s = StanceProfile(school_id="polarized", emphasis=DEFAULT_EMPHASIS, is_default=False)
        try:
            s.school_id = "x"  # type: ignore[misc]
            assert False, "StanceProfile should be frozen"
        except Exception:
            pass
