"""P4 (#285) UserMaterial model — storage for runner-uploaded coaching materials.

Fixtures here are synthetic test setup (exercising model shape, defaults, and the
many-per-user relationship), not ground truth about real coaching content.
"""

import uuid

from app.models import User, UserMaterial


def _make_user(db) -> User:
    user = User(email=f"material-test-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    db.commit()
    return user


def test_user_material_persists_with_required_fields(db):
    user = _make_user(db)
    material = UserMaterial(
        user_id=user.id,
        kind="plan",
        title="Spring Marathon Plan",
        filename="spring-plan.md",
        raw_text="# Week 1\nEasy 5k Monday.",
        content_hash="abc123",
    )
    db.add(material)
    db.commit()

    fetched = db.query(UserMaterial).filter(UserMaterial.id == material.id).one()
    assert fetched.user_id == user.id
    assert fetched.kind == "plan"
    assert fetched.title == "Spring Marathon Plan"
    assert fetched.filename == "spring-plan.md"
    assert fetched.raw_text == "# Week 1\nEasy 5k Monday."
    assert fetched.content_hash == "abc123"
    assert fetched.created_at is not None


def test_status_defaults_to_processing(db):
    user = _make_user(db)
    material = UserMaterial(
        user_id=user.id,
        kind="other",
        title="t",
        filename="t.md",
        raw_text="body",
        content_hash="h",
    )
    db.add(material)
    db.commit()

    assert material.status == "processing"


def test_distilled_and_provenance_are_null_until_distilled(db):
    user = _make_user(db)
    material = UserMaterial(
        user_id=user.id,
        kind="protocol",
        title="t",
        filename="t.md",
        raw_text="body",
        content_hash="h",
    )
    db.add(material)
    db.commit()

    assert material.distilled is None
    assert material.distill_model is None
    assert material.distilled_at is None


def test_distilled_json_roundtrips(db):
    user = _make_user(db)
    record = {
        "stance": "Patient aerobic base building.",
        "principles": ["Run easy most days", "Build the long run slowly"],
        "method_framing": "Weekly volume leads; quality is a late seasoning.",
        "emphasis_hints": ["easy-day discipline", "weekly volume"],
    }
    material = UserMaterial(
        user_id=user.id,
        kind="philosophy",
        title="My approach",
        filename="approach.md",
        raw_text="...",
        content_hash="h",
        status="active",
        distilled=record,
        distill_model="claude-haiku-4-5",
    )
    db.add(material)
    db.commit()

    fetched = db.query(UserMaterial).filter(UserMaterial.id == material.id).one()
    assert fetched.distilled == record
    assert fetched.status == "active"
    assert fetched.distill_model == "claude-haiku-4-5"


def test_multiple_materials_allowed_per_user(db):
    user = _make_user(db)
    for i in range(3):
        db.add(
            UserMaterial(
                user_id=user.id,
                kind="other",
                title=f"m{i}",
                filename=f"m{i}.md",
                raw_text="x",
                content_hash=f"h{i}",
            )
        )
    db.commit()

    count = db.query(UserMaterial).filter(UserMaterial.user_id == user.id).count()
    assert count == 3
