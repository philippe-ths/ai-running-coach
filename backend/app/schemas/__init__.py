"""
Pydantic schemas — barrel re-exports for backward compatibility.

Individual schema files live in backend/app/schemas/<domain>.py.
Import from here (e.g. ``from app.schemas import ActivityRead``) or
directly from the submodule.
"""

from app.schemas.user import UserCreate, UserRead  # noqa: F401
from app.schemas.activity import (  # noqa: F401
    ActivityBase,
    ActivityCreate,
    ActivityRead,
    ActivityIntentUpdate,
)
from app.schemas.profile import (  # noqa: F401
    UserProfileBase,
    UserProfileCreate,
    UserProfileRead,
)
from app.schemas.checkin import CheckInBase, CheckInCreate, CheckInRead  # noqa: F401
from app.schemas.sync import SyncResponse  # noqa: F401
from app.schemas.detail import (  # noqa: F401
    DerivedMetricRead,
    ActivityStreamRead,
    ActivityDetailRead,
)
from app.schemas.trends import (  # noqa: F401
    WeeklyDistancePoint,
    WeeklyTimePoint,
    TrendsResponse,
)
from app.schemas.coach import (  # noqa: F401
    CoachNextStep,
    CoachRisk,
    CoachQuestion,
    CoachReportMeta,
    CoachReportContent,
    CoachReportRead,
)

__all__ = [
    "UserCreate",
    "UserRead",
    "ActivityBase",
    "ActivityCreate",
    "ActivityRead",
    "ActivityIntentUpdate",
    "UserProfileBase",
    "UserProfileCreate",
    "UserProfileRead",
    "CheckInBase",
    "CheckInCreate",
    "CheckInRead",
    "SyncResponse",
    "DerivedMetricRead",
    "ActivityStreamRead",
    "ActivityDetailRead",
    "WeeklyDistancePoint",
    "WeeklyTimePoint",
    "PaceTrendPoint",
    "TrendsResponse",
    "CoachNextStep",
    "CoachRisk",
    "CoachQuestion",
    "CoachReportMeta",
    "CoachReportContent",
    "CoachReportRead",
]
