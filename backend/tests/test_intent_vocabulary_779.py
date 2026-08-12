"""#779: the stated-intent vocabulary lives once, in `services.intents`.

It used to exist twice — the frontend picker and the coach's proposed-action
guard — with the intent API taking free text, so nothing enforced the agreement.
A label in one copy and not the other failed quietly in both directions.

Two guarantees are pinned here:

1. Both consumers read the SAME list. The activity-detail read serves the runner's
   picker from `services.intents`, and the coach's `_canonical_intent` validates
   against it, so every label one side offers the other accepts.
2. The frontend holds no copy to drift. The structural guard below fails loudly in
   CI if the vocabulary is ever re-inlined into a frontend file — the failure the
   issue describes made impossible rather than merely fixed.

Row data is synthetic test setup: these tests are about which labels flow where,
not about any real activity's numbers.
"""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import Activity, User
from app.services import intents
from app.services.coach import proposed_actions

_BASE = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
# The directories the app is actually built from. node_modules is excluded by
# construction (it is not under any of these).
_FRONTEND_SOURCE_DIRS = ("app", "components", "lib", "scripts")


def _activity(db, *, type="Run", user_intent=None, sport_type=None) -> Activity:
    user = User(email=f"iv-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)

    activity = Activity(
        user_id=user.id,
        strava_activity_id=uuid.uuid4().int % 10**9,
        start_date=_BASE,
        type=type,
        name=type,
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=10.0,
        raw_summary={"sport_type": sport_type} if sport_type else {},
        user_intent=user_intent,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


# --- the picker reads the backend's vocabulary ------------------------------


def test_detail_read_serves_the_vocabulary_for_the_activitys_type(client, db):
    activity = _activity(db, type="Run")

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["intent_options"] == list(
        intents.INTENT_OPTIONS_BY_TYPE["Run"]
    )


def test_detail_read_falls_back_to_the_default_vocabulary_for_an_unlisted_type(
    client, db
):
    """A type with no list of its own (Strava has many) gets the generic labels
    rather than an empty picker."""
    activity = _activity(db, type="Kayaking")

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["intent_options"] == list(
        intents.INTENT_OPTIONS_BY_TYPE["Default"]
    )


def test_a_specialised_sport_type_gets_its_base_types_vocabulary(client, db):
    """The one deliberate behaviour change (#779).

    The picker used to key on Strava's fine-grained `sport_type`, which is not a
    vocabulary key for a trail run, so the runner was offered the generic Default
    labels. The coach's guard has always keyed on the stored `Activity.type`, so
    it could propose "Easy Run" on a run the picker had no way to display as a
    selection — the issue's failure, live. One key now, the coach's.

    Measured against production at the time of the change: 23 of 605 activities
    (21 TrailRun, 1 GravelRide, 1 Padel), each moving from Default to its base
    type's list.
    """
    activity = _activity(db, type="Run", sport_type="TrailRun")

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["intent_options"] == list(
        intents.INTENT_OPTIONS_BY_TYPE["Run"]
    )


# --- already-stored free-text intents stay readable -------------------------


def test_a_stored_intent_outside_the_vocabulary_is_still_offered(client, db):
    """Backwards compatibility. Activities carry intents written before the
    picker existed. A <select> whose value matches no option displays the wrong
    thing and overwrites it on the next save, so the stored value rides along."""
    activity = _activity(db, type="Run", user_intent="fartlek session")

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    options = resp.json()["intent_options"]
    assert "fartlek session" in options
    # It is appended, not substituted: the vocabulary itself is unchanged.
    assert options[:-1] == list(intents.INTENT_OPTIONS_BY_TYPE["Run"])


def test_a_stored_intent_inside_the_vocabulary_is_not_duplicated(client, db):
    activity = _activity(db, type="Run", user_intent="Tempo")

    resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["intent_options"] == list(
        intents.INTENT_OPTIONS_BY_TYPE["Run"]
    )


def test_a_legacy_intent_never_widens_what_the_coach_may_propose(db):
    """The runner's own stored oddity is displayable; it is not vocabulary. The
    coach still gets the canonical list, so it cannot echo one activity's legacy
    label onto another."""
    activity = _activity(db, type="Run", user_intent="fartlek session")

    assert "fartlek session" in intents.selectable_intent_options(
        activity.type, activity.user_intent
    )
    assert "fartlek session" not in intents.intent_options_for(activity.type)


# --- the two consumers cannot disagree --------------------------------------


def test_the_coach_accepts_every_label_the_picker_offers(db):
    """The round-trip the issue is about: a label the runner can select is a
    label the coach can propose, for every activity type.

    Failure here is the #768 symptom — the coach telling the runner it cannot set
    an intent for this activity type, which was simply false."""
    for activity_type in intents.INTENT_OPTIONS_BY_TYPE:
        activity = _activity(db, type=activity_type)
        for label in intents.selectable_intent_options(activity.type, None):
            assert (
                proposed_actions._canonical_intent(activity, label) == label
            ), f"the coach rejects {label!r}, which the picker offers for {activity_type}"


def test_the_coach_rejects_a_label_from_another_types_vocabulary(db):
    """The converse: the guard still discriminates, so the test above is not
    passing because everything is accepted."""
    walk = _activity(db, type="Walk")

    try:
        proposed_actions._canonical_intent(walk, "Tempo")
    except proposed_actions._InvalidIntent as exc:
        assert exc.allowed == list(intents.INTENT_OPTIONS_BY_TYPE["Walk"])
    else:
        raise AssertionError("a Run label was accepted for a Walk")


# --- the duplication cannot come back ---------------------------------------


def vocabulary_copies_in(source: str) -> list[str]:
    """Signs that a frontend file has re-inlined the intent vocabulary.

    Two signals, both cheap and both low-false-positive: the identifier the
    removed copy used, and any multi-word label. Single-word labels ("Race",
    "Workout") are ordinary UI words and are deliberately not matched, so this
    guard fires on a re-added vocabulary rather than on prose.
    """
    found = []
    if "OPTIONS_BY_TYPE" in source:
        found.append("declares an OPTIONS_BY_TYPE map")
    for labels in intents.INTENT_OPTIONS_BY_TYPE.values():
        for label in labels:
            if " " in label and re.search(rf"['\"`]{re.escape(label)}['\"`]", source):
                found.append(f"contains the literal {label!r}")
    return found


def test_no_frontend_source_file_holds_a_copy_of_the_vocabulary():
    """The single source of truth, enforced. The frontend renders the picker from
    what the activity-detail read serves; a re-inlined list is the exact defect
    #779 exists to remove, and it fails the build here rather than quietly at
    runtime when the two lists first disagree."""
    problems = []
    for directory in _FRONTEND_SOURCE_DIRS:
        root = _FRONTEND / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
                continue
            for copy in vocabulary_copies_in(path.read_text()):
                problems.append(f"{path.relative_to(_FRONTEND)} {copy}")

    assert not problems, (
        "the stated-intent vocabulary has been duplicated into the frontend. It "
        "lives in backend/app/services/intents.py and reaches the picker through "
        "ActivityDetailRead.intent_options:\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_the_duplication_guard_notices_a_re_added_vocabulary():
    """SENSITIVITY. A guard whose own ability to fail is untested is how the
    original defect survives a rewrite. This is the removed copy, verbatim."""
    source = (
        "const OPTIONS_BY_TYPE: Record<string, string[]> = {\n"
        '  Run: ["Easy Run", "Recovery", "Long Run", "Tempo"],\n'
        "};\n"
    )

    problems = vocabulary_copies_in(source)

    assert any("OPTIONS_BY_TYPE" in p for p in problems)
    assert any("Easy Run" in p for p in problems)


def test_the_duplication_guard_does_not_fire_on_ordinary_frontend_prose():
    """The other side of the line: it must not fire on a component that merely
    renders a label it was handed, or a guard people route around is worse than
    no guard."""
    source = (
        "const label = 'Race';\n"
        "{typeOptions.map(type => (<option key={type}>{type}</option>))}\n"
    )

    assert vocabulary_copies_in(source) == []
