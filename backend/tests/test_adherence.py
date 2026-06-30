"""M7 adherence learning loop — pure-module unit tests.

The module classifies a prior report's next_steps into recognised themes and
labels each against the runner's subsequent comparable activity (acted_on /
ignored / contradicted), abstaining whenever the step is unrecognised, no
comparable non-noisy run exists, or the advice is multi-intent. Explicit
pushback on the prior report flips a label to "disputed". All inputs are plain
data so the module stays pure (mirrors M6 perceived_effort).
"""

from app.services.coach.adherence import CandidateActivity, build_adherence


def _step(action, details="", why=""):
    return {"action": action, "details": details, "why": why}


def _easy_run(date, effort, *, confidence="high"):
    """An easy-INTENDED run (the runner labelled it easy) at the given effort."""
    return CandidateActivity(
        date=date,
        effort=effort,
        duration_class="standard",
        structure="continuous",
        is_race=False,
        confidence=confidence,
        user_intent="Easy Run",
    )


# --------------------------------------------------------------------------- #
# easy_discipline theme — the brief's canonical oracle
# --------------------------------------------------------------------------- #

def test_easy_discipline_acted_on():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy", "Zone 2 only")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "easy")],
        pushback=False,
    )
    assert len(ctx.outcomes) == 1
    o = ctx.outcomes[0]
    assert o.theme == "easy_discipline"
    assert o.label == "acted_on"
    assert o.overridden is False
    assert o.comparable_activity_date == "2026-02-03T10:00:00+00:00"
    assert o.basis  # non-empty evidence string
    assert ctx.prior_report_date == "2026-02-01T10:00:00+00:00"


def test_easy_discipline_contradicted():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "tempo")],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "contradicted"


def test_easy_discipline_ignored_on_moderate_drift():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "moderate")],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "ignored"


def test_easy_discipline_non_comparable_intent_abstains():
    """A subsequent run the runner MEANT as a workout is not a fair test of
    'keep easy runs easy' -> no label."""
    workout = CandidateActivity(
        date="2026-02-03T10:00:00+00:00", effort="tempo",
        duration_class="standard", structure="continuous", is_race=False,
        confidence="high", user_intent="Tempo",
    )
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[workout],
        pushback=False,
    )
    assert ctx.outcomes == []
    assert ctx.prior_report_date is None  # nothing to report -> no anchor date


def test_easy_discipline_low_confidence_is_noise_gated():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "tempo", confidence="low")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_easy_discipline_unlabelled_easy_run_acted_on():
    """This runner labels no intent; an unlabelled continuous easy run still earns
    acted_on so the signal is not dead on real data."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Take an easy recovery run", "20-30 min conversational")],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="easy", user_intent=None)],
        pushback=False,
    )
    assert ctx.outcomes[0].theme == "easy_discipline"
    assert ctx.outcomes[0].label == "acted_on"


def test_easy_discipline_unlabelled_hard_capped_to_ignored():
    """Without an explicit easy label we cannot rule out a deliberate workout, so
    a hard read is softened to 'ignored', never the accusatory 'contradicted'."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo",
                         structure="continuous", user_intent=None)],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "ignored"


def test_easy_discipline_excludes_declared_workout():
    """A run the runner declared a workout is never judged against easy advice."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo", user_intent="Tempo")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_measurement_advice_does_not_classify_as_add_quality():
    """Advice ABOUT executing/measuring a workout is not advice to DO one."""
    for action in (
        "Practice pace discipline in future tempo runs",
        "Use your watch's lap button for future interval sessions",
    ):
        ctx = build_adherence(
            prior_report_date="2026-02-01T10:00:00+00:00",
            prior_next_steps=[_step(action)],
            candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo")],
            pushback=False,
        )
        assert ctx.outcomes == [], f"{action!r} should not classify"


def test_more_measurement_advice_does_not_classify():
    """#165: extra real-data capture/testing phrasings must still abstain — the
    recall improvement must not start reading data-capture advice as add_quality."""
    for action, details in (
        ("Consider using structured intervals for future indoor rides",
         "use your bike computer's lap button or interval timer to track work and rest periods"),
        ("Consider using interval timing for structured rowing sessions",
         "use the rowing machine's interval function or manual lap button"),
        ("Consider heart rate zone calibration",
         "schedule a lactate threshold or ramp test to establish personalized training zones"),
        ("Consider using manual lap markers for structured indoor sessions",
         "press lap button between intervals or efforts to improve workout detection"),
    ):
        ctx = build_adherence(
            prior_report_date="2026-02-01T10:00:00+00:00",
            prior_next_steps=[{"action": action, "details": details, "why": ""}],
            candidates=[
                CandidateActivity(
                    date="2026-02-03T10:00:00+00:00", effort="tempo",
                    duration_class="standard", structure="continuous", is_race=False,
                    confidence="high", user_intent=None,
                ),
            ],
            pushback=False,
        )
        assert ctx.outcomes == [], f"{action!r} should not classify as a theme"


def test_easy_discipline_judges_first_comparable_run():
    """The verdict is the NEXT comparable run after the advice, not a later one."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[
            _easy_run("2026-02-03T10:00:00+00:00", "easy"),   # first -> acted_on
            _easy_run("2026-02-06T10:00:00+00:00", "hard"),   # later, ignored
        ],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "acted_on"
    assert ctx.outcomes[0].comparable_activity_date == "2026-02-03T10:00:00+00:00"


# --------------------------------------------------------------------------- #
# add_quality theme — window-evaluated (did they add a quality session at all)
# --------------------------------------------------------------------------- #

def _run(date, *, effort="easy", structure="continuous", duration_class="standard",
         is_race=False, confidence="high", user_intent=None):
    return CandidateActivity(
        date=date, effort=effort, duration_class=duration_class,
        structure=structure, is_race=is_race, confidence=confidence,
        user_intent=user_intent,
    )


def test_add_quality_acted_on_via_tempo_effort():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run", "20 min at threshold")],
        candidates=[_run("2026-02-04T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert ctx.outcomes[0].theme == "add_quality"
    assert ctx.outcomes[0].label == "acted_on"


def test_add_quality_acted_on_via_interval_structure():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add an interval session")],
        candidates=[_run("2026-02-04T10:00:00+00:00", effort="moderate", structure="intervals")],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "acted_on"


def test_add_quality_ignored_after_enough_chances():
    """Only call it ignored once the runner has had _MIN_OPPORTUNITY_RUNS (3)
    comparable runs and still added no quality."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[
            _run("2026-02-03T10:00:00+00:00", effort="easy"),
            _run("2026-02-05T10:00:00+00:00", effort="easy"),
            _run("2026-02-07T10:00:00+00:00", effort="easy"),
        ],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "ignored"


def test_add_quality_abstains_before_enough_chances():
    """A single easy run the day after advice is NOT 'ignored' (the runner has
    not reached the session yet) -> abstain, no false accusation."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[
            _run("2026-02-02T10:00:00+00:00", effort="easy"),
            _run("2026-02-04T10:00:00+00:00", effort="easy"),
        ],
        pushback=False,
    )
    assert ctx.outcomes == []


# --------------------------------------------------------------------------- #
# add_long_run theme — window-evaluated on duration_class
# --------------------------------------------------------------------------- #

def test_add_long_run_acted_on():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a long run", "build to 90 minutes")],
        candidates=[_run("2026-02-06T10:00:00+00:00", effort="easy", duration_class="long")],
        pushback=False,
    )
    assert ctx.outcomes[0].theme == "add_long_run"
    assert ctx.outcomes[0].label == "acted_on"


def test_add_long_run_ignored_after_enough_chances():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a long run")],
        candidates=[
            _run("2026-02-03T10:00:00+00:00", duration_class="standard"),
            _run("2026-02-05T10:00:00+00:00", duration_class="standard"),
            _run("2026-02-07T10:00:00+00:00", duration_class="standard"),
        ],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "ignored"


def test_add_long_run_abstains_before_enough_chances():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a long run")],
        candidates=[_run("2026-02-06T10:00:00+00:00", duration_class="standard")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_add_quality_acted_on_fires_immediately():
    """acted_on is NOT opportunity-gated: doing the session once is enough."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[_run("2026-02-02T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "acted_on"


# --------------------------------------------------------------------------- #
# #165 recall: real-data additive-quality phrasings now classify, and the
# action-first rule rescues a quality instruction whose DETAILS incidentally
# mention easy/recovery (the second-theme-in-the-rationale false abstain).
# --------------------------------------------------------------------------- #

def test_add_quality_real_phrasings_classify():
    """Real production next_step actions the coach uses to ADD a quality session
    that the v1 keyword set missed."""
    for action in (
        "Consider adding a quality running session",
        "Add one quality session this week",
        "Plan a tempo run",
    ):
        ctx = build_adherence(
            prior_report_date="2026-02-01T10:00:00+00:00",
            prior_next_steps=[_step(action)],
            candidates=[_run("2026-02-02T10:00:00+00:00", effort="tempo")],
            pushback=False,
        )
        assert len(ctx.outcomes) == 1, f"{action!r} should classify"
        assert ctx.outcomes[0].theme == "add_quality", action


def test_add_quality_action_survives_easy_mention_in_details():
    """#165: the imperative is add_quality; the details name "easy recovery rides"
    only as the rest BETWEEN quality work. Action-first classification trusts the
    instruction instead of abstaining as multi-intent on the incidental mention."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step(
            "Plan your next quality session",
            details="wait 2-3 days before another moderate or hard effort, "
                    "focusing on easy recovery rides in between",
        )],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert len(ctx.outcomes) == 1
    assert ctx.outcomes[0].theme == "add_quality"
    assert ctx.outcomes[0].label == "acted_on"


def test_multi_intent_in_the_action_itself_still_abstains():
    """Action-first must NOT defeat the multi-intent guard: when the imperative
    ITSELF names two themes, the step is genuinely ambiguous -> abstain."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run but keep your easy runs easy")],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_caution_in_details_still_abstains_under_action_first():
    """The negation guard runs on the full text, so a caution buried in the
    details abstains even when the action keyword looks positive."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step(
            "Plan your next quality session",
            details="but hold off until the knee settles",
        )],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert ctx.outcomes == []


# --------------------------------------------------------------------------- #
# #579: a deliberate rest/recovery window must abstain, never call "ignored".
# A recovery-band effort, a recovery/rest intent, or a fired discount signal is
# the runner managing fatigue, not a missed opportunity to add quality/distance.
# --------------------------------------------------------------------------- #

def test_add_quality_abstains_when_window_is_all_recovery():
    """The runner deliberately recovered across the whole window (recovery-band
    effort). Even at >= _MIN_OPPORTUNITY_RUNS comparable runs, there was no real
    opportunity to add a quality session -> abstain, not 'ignored'."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[
            _run("2026-02-03T10:00:00+00:00", effort="recovery"),
            _run("2026-02-05T10:00:00+00:00", effort="recovery"),
            _run("2026-02-07T10:00:00+00:00", effort="recovery"),
        ],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_add_quality_abstains_when_recovery_intent_leaves_too_few_opportunities():
    """Two of three comparable runs were declared recovery/rest; only one genuine
    opportunity remains, below the quorum -> abstain."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[
            _run("2026-02-03T10:00:00+00:00", effort="easy", user_intent="Recovery"),
            _run("2026-02-05T10:00:00+00:00", effort="easy", user_intent="Rest day jog"),
            _run("2026-02-07T10:00:00+00:00", effort="easy"),
        ],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_add_quality_abstains_when_window_is_confounded():
    """Every comparable run fired a discount signal (heat/hills/stimulant). A
    confounded run is exculpatory, not a missed opportunity -> abstain."""
    confounded = [
        CandidateActivity(
            date=f"2026-02-0{d}T10:00:00+00:00", effort="easy",
            duration_class="standard", structure="continuous", is_race=False,
            confidence="high", user_intent=None, discount_signals_fired=True,
        )
        for d in (3, 5, 7)
    ]
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=confounded,
        pushback=False,
    )
    assert ctx.outcomes == []


def test_add_quality_still_ignored_with_enough_genuine_opportunity():
    """A rest day in the window does NOT immunise a genuinely-ignored advice: with
    >= _MIN_OPPORTUNITY_RUNS real (non-recovery, non-confounded) opportunity runs
    and still no quality, 'ignored' remains the fair read (no regression)."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[
            _run("2026-02-02T10:00:00+00:00", effort="recovery"),  # exculpatory rest
            _run("2026-02-03T10:00:00+00:00", effort="easy"),
            _run("2026-02-05T10:00:00+00:00", effort="easy"),
            _run("2026-02-07T10:00:00+00:00", effort="easy"),
        ],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "ignored"


def test_add_quality_acted_on_survives_confounded_day():
    """The exculpatory exclusion only suppresses the NEGATIVE verdict: a real
    quality session, even on a confounded day, still reads 'acted_on'."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run")],
        candidates=[
            CandidateActivity(
                date="2026-02-03T10:00:00+00:00", effort="tempo",
                duration_class="standard", structure="continuous", is_race=False,
                confidence="high", user_intent=None, discount_signals_fired=True,
            ),
        ],
        pushback=False,
    )
    assert ctx.outcomes[0].label == "acted_on"


def test_add_long_run_abstains_when_window_is_all_recovery():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a long run")],
        candidates=[
            _run("2026-02-03T10:00:00+00:00", effort="recovery", duration_class="standard"),
            _run("2026-02-05T10:00:00+00:00", effort="recovery", duration_class="standard"),
            _run("2026-02-07T10:00:00+00:00", effort="recovery", duration_class="standard"),
        ],
        pushback=False,
    )
    assert ctx.outcomes == []


# --------------------------------------------------------------------------- #
# abstention: unrecognised, multi-intent, empty
# --------------------------------------------------------------------------- #

def test_unrecognised_step_abstains():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Stay hydrated and sleep well")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "easy")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_multi_intent_step_abstains():
    """A step that matches more than one theme is ambiguous -> no label."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Add a tempo run but keep your easy runs easy")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "easy")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_caution_not_to_add_intensity_abstains():
    """A step warning AGAINST a theme must not be labelled acted_on when the
    runner does the warned-against thing (no inverted verdict)."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Be careful not to add intensity too soon")],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_hold_off_extending_long_run_abstains():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Hold off on extending the long run", "until the knee settles")],
        candidates=[_run("2026-02-06T10:00:00+00:00", duration_class="long")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_why_field_does_not_drive_classification():
    """The rationale (why) must not re-theme a step whose action is unrelated."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Focus on running form", why="so you can add a tempo run later")],
        candidates=[_run("2026-02-03T10:00:00+00:00", effort="tempo")],
        pushback=False,
    )
    assert ctx.outcomes == []


def test_no_candidates_abstains():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[],
        pushback=False,
    )
    assert ctx.outcomes == []
    assert ctx.prior_report_date is None


def test_no_prior_report_is_empty():
    ctx = build_adherence(
        prior_report_date=None, prior_next_steps=[], candidates=[], pushback=False,
    )
    assert ctx.outcomes == []
    assert ctx.prior_report_date is None


# --------------------------------------------------------------------------- #
# override: explicit pushback flips the implicit label to "disputed"
# --------------------------------------------------------------------------- #

def test_pushback_overrides_implicit_label():
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[_easy_run("2026-02-03T10:00:00+00:00", "tempo")],  # implicitly contradicted
        pushback=True,
    )
    o = ctx.outcomes[0]
    assert o.overridden is True
    assert o.label == "disputed"
    # the implicit read is preserved in the basis for transparency
    assert "contradicted" in o.basis.lower()


def test_pushback_with_no_comparable_run_surfaces_nothing():
    """Pushback only matters when there is an implicit label to flip; with no
    comparable run there is no outcome to override."""
    ctx = build_adherence(
        prior_report_date="2026-02-01T10:00:00+00:00",
        prior_next_steps=[_step("Keep your easy runs easy")],
        candidates=[],
        pushback=True,
    )
    assert ctx.outcomes == []
