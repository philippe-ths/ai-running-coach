"""P4 (#285) User materials API — upload guards, dedup/retry, list, poll, archive,
delete, and per-user scoping.

Inputs here are synthetic test setup (small markdown blobs and a stubbed enqueue)
exercising the endpoint branches; none represents real captured coaching content.
The background distillation is stubbed out (its own behaviour is covered in
test_material_distiller.py); these tests assert the HTTP surface and the structural
guards.
"""

import uuid
from unittest.mock import patch

from app.core.config import settings
from app.models import User, UserMaterial


def _upload(client, content: bytes, *, kind="other", title=None, filename="material.md"):
    data = {"kind": kind}
    if title is not None:
        data["title"] = title
    return client.post(
        "/api/coach/materials",
        files={"file": (filename, content, "text/markdown")},
        data=data,
    )


def test_upload_returns_202_processing_and_enqueues(client):
    with patch("app.api.materials.enqueue_distillation") as enqueue:
        resp = _upload(client, b"# Plan\nRun easy on Mondays.", kind="plan", title="Spring")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "processing"
    assert body["kind"] == "plan"
    assert body["title"] == "Spring"
    assert body["distilled"] is None
    assert "raw_text" not in body  # the untrusted source is never echoed back
    enqueue.assert_called_once()


def test_upload_defaults_title_from_filename(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"content", kind="other", filename="my-philosophy.md")
    assert resp.status_code == 202
    assert resp.json()["title"] == "my-philosophy"


def test_upload_rejects_non_utf8(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"\xff\xfe\xfa\x80bad bytes", kind="other")
    assert resp.status_code == 400
    assert "UTF-8" in resp.json()["detail"]


def test_upload_rejects_empty(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"   \n\t  ", kind="other")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr(settings, "USER_MATERIAL_MAX_BYTES", 10)
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"x" * 100, kind="other")
    assert resp.status_code == 413


def test_upload_rejected_at_edge_before_endpoint_when_body_exceeds_request_cap(client):
    # #705: a body over the wired edge request cap (MAX_REQUEST_BODY_BYTES, 1 MiB)
    # is rejected before the endpoint (and before the form parser spools it),
    # regardless of the endpoint's own 256 KB cap. The middleware captures the cap
    # at construction, so this exercises the real wired value with an oversize body.
    oversize = b"x" * (settings.MAX_REQUEST_BODY_BYTES + 1)
    with patch("app.api.materials.enqueue_distillation") as enqueue:
        resp = _upload(client, oversize, kind="other")
    assert resp.status_code == 413
    enqueue.assert_not_called()  # never reached the endpoint


def test_upload_truncates_long_title(client):
    # #706: the reflected title is length-bounded at rest.
    long_title = "T" * 5000
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"content", kind="other", title=long_title)
    assert resp.status_code == 202
    assert len(resp.json()["title"]) == 200


def test_upload_truncates_long_filename(client):
    # #706: the reflected filename (its default-title derivation and the stored
    # value) is length-bounded at rest.
    long_name = "f" * 5000 + ".md"
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"content", kind="other", filename=long_name)
    assert resp.status_code == 202
    assert len(resp.json()["filename"]) <= 255


def test_upload_rejects_unknown_kind(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"content", kind="malware")
    assert resp.status_code == 422  # enum validation on the kind form field


def test_upload_dedups_identical_active_material(client, db):
    content = b"# My method\nLots of easy miles."
    with patch("app.api.materials.enqueue_distillation") as enqueue:
        first = _upload(client, content, kind="philosophy")
        material_id = first.json()["id"]
        # Simulate the distiller having completed.
        row = db.query(UserMaterial).filter(UserMaterial.id == uuid.UUID(material_id)).one()
        row.status = "active"
        row.distilled = {
            "stance": "s",
            "principles": [],
            "method_framing": "m",
            "emphasis_hints": [],
        }
        db.commit()

        second = _upload(client, content, kind="philosophy")

    assert first.status_code == 202
    assert second.status_code == 200  # dedup hit on an active material
    assert second.json()["id"] == material_id
    enqueue.assert_called_once()  # the active dedup did NOT re-enqueue


def test_upload_retries_a_failed_material(client, db):
    content = b"# Protocol\nStretch daily."
    with patch("app.api.materials.enqueue_distillation") as enqueue:
        first = _upload(client, content, kind="protocol")
        material_id = first.json()["id"]
        row = db.query(UserMaterial).filter(UserMaterial.id == uuid.UUID(material_id)).one()
        row.status = "failed"
        db.commit()

        second = _upload(client, content, kind="protocol")

    assert second.status_code == 202
    assert second.json()["id"] == material_id
    assert second.json()["status"] == "processing"  # reset for retry
    assert enqueue.call_count == 2  # initial + the retry


def test_count_cap_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "USER_MATERIAL_MAX_COUNT", 1)
    with patch("app.api.materials.enqueue_distillation"):
        first = _upload(client, b"material one", kind="other")
        second = _upload(client, b"material two (different)", kind="other")
    assert first.status_code == 202
    assert second.status_code == 409


def test_list_excludes_archived_by_default_and_includes_with_flag(client, db):
    with patch("app.api.materials.enqueue_distillation"):
        a = _upload(client, b"alpha", kind="other", title="alpha")
        b = _upload(client, b"beta", kind="other", title="beta")
    # Archive beta.
    client.post(f"/api/coach/materials/{b.json()['id']}/archive")

    default_list = client.get("/api/coach/materials").json()
    assert {m["title"] for m in default_list} == {"alpha"}

    full_list = client.get("/api/coach/materials?include_archived=true").json()
    assert {m["title"] for m in full_list} == {"alpha", "beta"}


def test_get_one_polls_status_and_404s_for_unknown(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"poll me", kind="other")
    material_id = resp.json()["id"]

    got = client.get(f"/api/coach/materials/{material_id}")
    assert got.status_code == 200
    assert got.json()["id"] == material_id

    missing = client.get(f"/api/coach/materials/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_archive_sets_status_archived(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"archive me", kind="other")
    material_id = resp.json()["id"]

    archived = client.post(f"/api/coach/materials/{material_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


def test_delete_removes_the_row(client):
    with patch("app.api.materials.enqueue_distillation"):
        resp = _upload(client, b"delete me", kind="other")
    material_id = resp.json()["id"]

    deleted = client.delete(f"/api/coach/materials/{material_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/coach/materials/{material_id}").status_code == 404


def test_list_is_scoped_to_the_current_user(client, db):
    # The first upload auto-creates the local (current) user + their material.
    with patch("app.api.materials.enqueue_distillation"):
        _upload(client, b"# mine\nrun easy", kind="plan", title="mine")
    # Another user's material must never appear in this runner's list.
    other = User(email=f"other-{uuid.uuid4().hex[:6]}@example.com")
    db.add(other)
    db.commit()
    db.add(
        UserMaterial(
            user_id=other.id,
            kind="other",
            title="not-mine",
            filename="x.md",
            raw_text="another runner's private content",
            content_hash="other-hash",
            status="active",
        )
    )
    db.commit()

    listed = client.get("/api/coach/materials").json()
    assert len(listed) == 1  # only the current user's material, not both
    assert listed[0]["title"] == "mine"
