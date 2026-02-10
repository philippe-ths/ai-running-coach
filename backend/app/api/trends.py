"""
API router for /api/trends — aggregated activity data for trend charts.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.trends import TrendsResponse
from app.services.trends import (
    get_available_types,
    get_trends_report,
)

router = APIRouter()


@router.get("/trends/types", response_model=List[str])
def list_activity_types(db: Session = Depends(get_db)):
    """Return distinct activity types available for filtering."""
    return get_available_types(db)


@router.get("/trends", response_model=TrendsResponse)
def get_trends(
    range: str = Query("30D", description="Time range: 7D, 30D, 3M, 6M, 1Y, ALL"),
    types: Optional[List[str]] = Query(None, description="Activity types to include (multi-select)"),
    db: Session = Depends(get_db),
):
    return get_trends_report(db, range, types)
