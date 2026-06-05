"""M1 hardening: fixes from the adversarial review of N3/N4."""

from app.schemas.coach import CoachReportContent
from app.services.analysis.discount_signals import compute_discount_signals


def _base(**extra):
    return CoachReportContent(
        key_takeaways=[{"text": "a"}],
        next_steps=[{"action": "x", "details": "y", "why": "z"}],
        **extra,
    )


def test_lead_argument_bare_string_coerced():
    """A bare-string lead_argument (mirroring legacy key_takeaways) must coerce,
    not force a fallback."""
    c = _base(lead_argument="Strong aerobic run, HR stayed easy.")
    assert c.lead_argument is not None
    assert c.lead_argument.text == "Strong aerobic run, HR stayed easy."


def test_lead_argument_string_evidence_coerced():
    """String evidence on lead_argument must coerce to structured refs like it
    does for key_takeaways/next_steps."""
    c = _base(lead_argument={"text": "t", "evidence": "metrics.hr_drift=7.2"})
    assert isinstance(c.lead_argument.evidence, list)
    assert c.lead_argument.evidence[0].field == "metrics.hr_drift"


def test_non_numeric_temperature_treated_as_unknown():
    """A stray non-numeric average_temp must degrade to 'unknown', never raise
    and never fabricate a heat confound."""
    r = compute_discount_signals(
        average_temp="warm", is_hilly=False, hr_drift=6.0, stimulant_use=False
    )
    assert r is not None
    assert r["likely_inflated_by"] == []
    assert r["confidence"] == "low"
    assert r["temperature_c"] is None
