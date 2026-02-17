from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.coach import CoachReportRead
from app.services.coach.service import get_or_generate_coach_report

router = APIRouter()


@router.get(
    "/activities/{activity_id}/coach-report",
    response_model=CoachReportRead,
)
async def get_coach_report(
    activity_id: UUID,
    db: Session = Depends(get_db),
):
    report = await get_or_generate_coach_report(db, str(activity_id))
    if not report:
        raise HTTPException(
            status_code=404,
            detail="Activity not found or metrics not yet computed.",
        )
    return report
