from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.coach import (
    CoachNextStep,
    CoachQuestion,
    CoachReportContent,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
    CoachRisk,
    CoachTakeaway,
)
from app.services.notifications.email_template import render_coach_report_email


def _build_report(*, headline: str = "Easy", confidence: str = "medium") -> CoachReportRead:
    return CoachReportRead(
        id=uuid4(),
        activity_id=uuid4(),
        report=CoachReportContent(
            key_takeaways=[
                CoachTakeaway(text="Effort stayed in zone 2 throughout."),
                CoachTakeaway(text="HR drift was minimal at 2.1%."),
            ],
            next_steps=[
                CoachNextStep(
                    action="Add a tempo run mid-week",
                    details="Target 4km at threshold pace.",
                    why="To extend lactate clearance.",
                )
            ],
            risks=[
                CoachRisk(
                    flag="cadence_low",
                    explanation="Cadence averaged 162 spm.",
                    mitigation="Consider strides 1-2x/week.",
                )
            ],
            questions=[
                CoachQuestion(
                    question="How did sleep feel last night?",
                    reason="Helps calibrate effort guidance.",
                )
            ],
        ),
        meta=CoachReportMeta(
            confidence=confidence,
            model_id="claude-sonnet-4-6",
            prompt_id="coach_report_v1",
            schema_version="1.1",
            input_hash="abc123",
            generated_at=datetime.now(timezone.utc),
        ),
        debug=CoachReportDebug(context_pack={}, system_prompt=""),
        created_at=datetime.now(timezone.utc),
    )


def test_subject_includes_class_and_distance():
    # #338: the confidence rating is no longer surfaced to the runner, so the
    # subject is the label plus distance only (the value still lives in meta).
    report = _build_report(headline="Easy Run", confidence="medium")
    subject, _html, _text = render_coach_report_email(
        report=report,
        headline="Easy Run",
        distance_m=8200,
        app_base_url="http://localhost:3000",
    )
    assert subject == "Easy Run — 8.2km"


def test_subject_does_not_append_literal_run_word():
    """The classifier returns labels that already include the activity noun
    (e.g. 'Easy Run', 'Indoor Ride'). The template must not append ' run'."""
    report = _build_report()
    subject, html, text = render_coach_report_email(
        report=report,
        headline="Indoor Ride",
        distance_m=0,
        app_base_url="http://localhost:3000",
    )
    assert "Ride run" not in subject
    assert "Ride run" not in html
    assert "Ride run" not in text


def test_subject_drops_distance_when_zero():
    report = _build_report()
    subject, html, text = render_coach_report_email(
        report=report,
        headline="Indoor Ride",
        distance_m=0,
        app_base_url="http://localhost:3000",
    )
    assert subject == "Indoor Ride"
    assert "0.0km" not in subject
    assert "0.0km" not in html
    assert "0.0km" not in text


def test_html_contains_all_sections_and_app_link():
    report = _build_report()
    _subject, html, _text = render_coach_report_email(
        report=report,
        headline="Easy",
        distance_m=8200,
        app_base_url="http://localhost:3000",
    )
    assert "Effort stayed in zone 2 throughout." in html
    assert "Add a tempo run mid-week" in html
    assert "Target 4km at threshold pace." in html
    assert "cadence_low" in html
    assert "How did sleep feel last night?" in html
    assert (
        f'href="http://localhost:3000/activity/{report.activity_id}"' in html
    )


def test_text_body_contains_takeaways_and_link():
    report = _build_report()
    _subject, _html, text = render_coach_report_email(
        report=report,
        headline="Easy",
        distance_m=8200,
        app_base_url="http://localhost:3000",
    )
    assert "Effort stayed in zone 2 throughout." in text
    assert "Add a tempo run mid-week" in text
    assert f"http://localhost:3000/activity/{report.activity_id}" in text


def test_distance_rounded_to_one_decimal():
    report = _build_report()
    subject, _html, _text = render_coach_report_email(
        report=report,
        headline="Tempo Run",
        distance_m=5347,
        app_base_url="http://localhost:3000",
    )
    assert subject == "Tempo Run — 5.3km"


def test_confidence_rating_is_not_surfaced_anywhere():
    """#338 (widened): the internal confidence rating must not reach the runner
    through the email channel either, in the subject, the HTML body, or the text
    body. The value still exists in report.meta; it is just never rendered."""
    report = _build_report(headline="Easy Run", confidence="high")
    subject, html, text = render_coach_report_email(
        report=report,
        headline="Easy Run",
        distance_m=8200,
        app_base_url="http://localhost:3000",
    )
    assert "confidence" not in subject.lower()
    assert "confidence" not in html.lower()
    assert "confidence" not in text.lower()
