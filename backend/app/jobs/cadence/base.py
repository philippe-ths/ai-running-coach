"""The post-activity cadence interface.

Lives in its own module so the three adapter modules can import the base without
importing the package `__init__` that imports them (#696).
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Activity, Block
from app.services.notifications import Notification
from app.services.notifications.port import NotifierPort


class PostActivityCadence:
    """Interface for a post-activity cadence. Each method handles one event; the
    base no-ops every event, so an adapter only overrides the ones it acts on."""

    async def on_ingest(
        self, *, db: Session, activity: Activity, block: Block, notifier: NotifierPort
    ) -> Optional[Notification]:
        return None

    async def on_block_complete(
        self, *, db: Session, block_id: str, activity_id: str, notifier: NotifierPort
    ) -> Optional[Notification]:
        return None

    def on_reply(self, *, db: Session, activity_id) -> bool:
        return False

    def on_done(self, *, db: Session, activity_id) -> bool:
        return False
