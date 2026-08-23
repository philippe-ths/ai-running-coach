"""
SQLAlchemy ORM models — barrel re-exports for backward compatibility.

Individual model files live in backend/app/models/<name>.py.
Import from here (e.g. ``from app.models import Activity``) or directly
from the submodule when you only need one model.
"""

from app.models.base import generate_uuid  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.strava_account import StravaAccount  # noqa: F401
from app.models.activity import Activity  # noqa: F401
from app.models.activity_stream import ActivityStream  # noqa: F401
from app.models.derived_metric import DerivedMetric  # noqa: F401
from app.models.user_profile import UserProfile  # noqa: F401
from app.models.checkin import CheckIn  # noqa: F401
from app.models.coach_report import CoachReport  # noqa: F401
from app.models.coach_chat_message import CoachChatMessage  # noqa: F401
from app.models.runner_baseline import RunnerBaseline  # noqa: F401
from app.models.block import Block  # noqa: F401
from app.models.exchange import Exchange  # noqa: F401
from app.models.coaching_relationship import CoachingRelationship  # noqa: F401
from app.models.strava_import import StravaImport  # noqa: F401
from app.models.user_material import UserMaterial  # noqa: F401
from app.models.runner_memory import RunnerMemory  # noqa: F401
from app.models.thread import Thread  # noqa: F401
from app.models.goal_race import GoalRace  # noqa: F401
from app.models.training_plan import TrainingPlan  # noqa: F401
from app.models.planned_session import PlannedSession  # noqa: F401
from app.models.period_report import PeriodReport  # noqa: F401

__all__ = [
    "generate_uuid",
    "User",
    "StravaAccount",
    "Activity",
    "ActivityStream",
    "DerivedMetric",
    "UserProfile",
    "CheckIn",
    "CoachReport",
    "CoachChatMessage",
    "RunnerBaseline",
    "Block",
    "Exchange",
    "CoachingRelationship",
    "StravaImport",
    "UserMaterial",
    "RunnerMemory",
    "Thread",
    "GoalRace",
    "TrainingPlan",
    "PlannedSession",
    "PeriodReport",
]
