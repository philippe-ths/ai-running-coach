"""P4 (#285) User materials API — upload, list, poll, archive, delete.

The FIRST untrusted-input surface in the product. The upload guards are STRUCTURAL
only (ADR 0017 — containment, not detection): a per-file byte cap, a UTF-8 decode,
a per-user count cap, and a kind-enum check. No content scanning. The real safety
guarantee lives downstream in the distiller (structured-output-only + strict
coercion) and, in slice 2, in the pack/prompt/validator; this layer just bounds the
shape and size of what enters.

Every query is scoped to the current runner's `user_id` (resolved from the Clerk
session by `require_current_user`, ADR 0022); per-user scoping is the exfiltration
defence under multi-user. The raw text is never echoed back — `UserMaterialRead` omits it.
"""

import hashlib
import logging
from typing import Optional

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.deps import DbSession, MaterialsOwnerId, OwnedMaterial
from app.core.config import settings
from app.models import UserMaterial
from app.schemas.material import MaterialKind, UserMaterialRead
from app.services.coach.material_distiller import enqueue_distillation

logger = logging.getLogger(__name__)

router = APIRouter()

# Statuses that count against the per-user cap and shadow a re-upload for dedup.
# `archived` rows are excluded: a runner who archived a material has chosen to set
# it aside, so it neither blocks new uploads nor dedups against a fresh one.
_LIVE_STATUSES = ("processing", "active", "failed")

# Bound the two reflected text fields (#706). `title` (a Form field) and
# `filename` (from the multipart part) are echoed verbatim in list/detail
# responses; the file BODY is capped but these were not. Cap them like the
# distilled fields already are, so a future non-escaping consumer cannot inherit
# stored oversize/XSS content and responses stay bounded. Truncate (do not
# reject): an over-long title/filename should not fail an otherwise-valid upload.
_MAX_TITLE_LEN = 200
_MAX_FILENAME_LEN = 255


def _default_title(filename: str) -> str:
    name = (filename or "material").rsplit("/", 1)[-1]
    for ext in (".markdown", ".md"):
        if name.lower().endswith(ext):
            return name[: -len(ext)] or name
    return name


def _owned_query(db: Session, user_id):
    return db.query(UserMaterial).filter(UserMaterial.user_id == user_id)


@router.post(
    "/coach/materials",
    response_model=UserMaterialRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_material(
    response: Response,
    db: DbSession,
    user_id: MaterialsOwnerId,
    file: UploadFile = File(...),
    kind: MaterialKind = Form(...),
    title: Optional[str] = Form(None),
):
    """Accept one markdown coaching material, store the raw text, and kick off
    background distillation. Returns 202 with status=processing; the frontend polls
    `GET /coach/materials/{id}` until status flips to `active` (or `failed`).

    Structural guards only (UTF-8, size cap, count cap, kind enum). Identical
    re-uploads dedup on `content_hash`; re-uploading content that previously FAILED
    re-runs the distillation on the same row.
    """
    # Size cap, bounded read: read at most cap+1 bytes so an oversize file never
    # loads fully into memory before we reject it.
    raw_bytes = await file.read(settings.USER_MATERIAL_MAX_BYTES + 1)
    if len(raw_bytes) > settings.USER_MATERIAL_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Material exceeds the {settings.USER_MATERIAL_MAX_BYTES}-byte limit."
            ),
        )

    # UTF-8 only — the untrusted surface stays plain text (no binary parser).
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material must be valid UTF-8 text.",
        )
    if not raw_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material is empty.",
        )

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    # Cap the two reflected fields at rest (#706). `_default_title` derives from the
    # filename, so bound both the resolved title and the stored filename.
    resolved_title = ((title or "").strip() or _default_title(file.filename))[
        :_MAX_TITLE_LEN
    ]
    resolved_filename = (file.filename or "material.md")[:_MAX_FILENAME_LEN]

    # Dedup / retry on content_hash among the runner's live (non-archived) materials.
    existing = (
        _owned_query(db, user_id)
        .filter(
            UserMaterial.content_hash == content_hash,
            UserMaterial.status.in_(_LIVE_STATUSES),
        )
        .order_by(UserMaterial.created_at.desc())
        .first()
    )
    if existing is not None:
        if existing.status == "active":
            response.status_code = status.HTTP_200_OK
            return existing
        if existing.status == "failed":
            # Retry the distillation on the same row (content is identical).
            existing.status = "processing"
            db.add(existing)
            db.commit()
            db.refresh(existing)
            enqueue_distillation(existing.id)
        # processing (or just-retried): already queued, nothing to re-enqueue.
        return existing

    # New material: enforce the per-user count cap on live materials.
    live_count = (
        _owned_query(db, user_id)
        .filter(UserMaterial.status.in_(_LIVE_STATUSES))
        .count()
    )
    if live_count >= settings.USER_MATERIAL_MAX_COUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You have reached the limit of {settings.USER_MATERIAL_MAX_COUNT} "
                "materials. Archive or delete one before uploading another."
            ),
        )

    material = UserMaterial(
        user_id=user_id,
        kind=kind.value,
        title=resolved_title,
        filename=resolved_filename,
        raw_text=raw_text,
        content_hash=content_hash,
        status="processing",
    )
    db.add(material)
    db.commit()
    db.refresh(material)

    enqueue_distillation(material.id)
    return material


@router.get("/coach/materials", response_model=list[UserMaterialRead])
def list_materials(
    db: DbSession,
    user_id: MaterialsOwnerId,
    include_archived: bool = False,
):
    """List the runner's materials, most recent first. Archived materials are
    excluded unless `include_archived=true`."""
    q = _owned_query(db, user_id)
    if not include_archived:
        q = q.filter(UserMaterial.status != "archived")
    return q.order_by(UserMaterial.created_at.desc()).all()


@router.get("/coach/materials/{material_id}", response_model=UserMaterialRead)
def get_material(material: OwnedMaterial):
    """Fetch one material (the status-poll endpoint the frontend hits until active)."""
    return material


@router.post("/coach/materials/{material_id}/archive", response_model=UserMaterialRead)
def archive_material(
    material: OwnedMaterial,
    db: DbSession,
):
    """Soft-hide a material (status=archived). Reversible by re-uploading the same
    content; the distilled record is retained on the row."""
    material.status = "archived"
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.delete("/coach/materials/{material_id}", status_code=204)
def delete_material(
    material: OwnedMaterial,
    db: DbSession,
):
    """Hard-delete a material (raw text and distilled record removed)."""
    db.delete(material)
    db.commit()
