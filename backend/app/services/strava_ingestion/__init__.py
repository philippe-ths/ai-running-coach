from app.services.strava_ingestion.http_adapter import HTTPStravaAdapter
from app.services.strava_ingestion.in_memory_adapter import InMemoryStravaAdapter
from app.services.strava_ingestion.ingestion import (
    ensure_valid_access_token,
    ingest_activity_by_id,
    ingest_recent_activities,
    refetch_streams,
    upsert_activity,
)
from app.services.strava_ingestion.port import StravaPort, StravaRateLimited, Tokens

_default_port: StravaPort | None = None


def get_strava_port() -> StravaPort:
    """Return the active Strava port. Lazily creates the production HTTP adapter."""
    global _default_port
    if _default_port is None:
        _default_port = HTTPStravaAdapter()
    return _default_port


def set_strava_port(port: StravaPort | None) -> None:
    """Override the active Strava port. Pass None to reset to the default."""
    global _default_port
    _default_port = port


__all__ = [
    "HTTPStravaAdapter",
    "InMemoryStravaAdapter",
    "StravaPort",
    "StravaRateLimited",
    "Tokens",
    "ensure_valid_access_token",
    "get_strava_port",
    "ingest_activity_by_id",
    "ingest_recent_activities",
    "refetch_streams",
    "set_strava_port",
    "upsert_activity",
]
