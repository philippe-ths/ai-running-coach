import json
import hashlib
from typing import Dict, Any, Optional, Type
from pydantic import BaseModel

from app.core.queue import redis_conn
from app.schemas import (
    VerdictScorecardResponse,
    StoryResponse,
    LeverResponse,
    NextStepsResponse,
    QuestionResponse
)

# Increment these to invalidate all caches
SCHEMA_VERSION = "1.0"
PROMPT_VERSION = "1.0"
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 Days

def compute_stable_hash(data: Any) -> str:
    """
    Computes a stable SHA256 hash of any JSON-serializable data.
    Sorts keys to ensure deterministic output.
    """
    canonical_json = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

def _make_key(section: str, data_hash: str) -> str:
    return f"verdict:v3:{section}:{SCHEMA_VERSION}:{PROMPT_VERSION}:{data_hash}"

def get_cached_section(
    section: str, 
    input_data: Any,
    model_cls: Type[BaseModel]
) -> Optional[BaseModel]:
    """Retrieve section from Redis if exists."""
    if not redis_conn: 
        return None
        
    data_hash = compute_stable_hash(input_data)
    key = _make_key(section, data_hash)
    
    try:
        cached_json = redis_conn.get(key)
        if cached_json:
            data = json.loads(cached_json)
            return model_cls(**data)
    except Exception:
        # Silently fail on cache read errors to allow generation to proceed
        return None
    return None

def set_cached_section(
    section: str, 
    input_data: Any, 
    response_model: BaseModel
):
    """Store section in Redis."""
    if not redis_conn:
        return

    data_hash = compute_stable_hash(input_data)
    key = _make_key(section, data_hash)
    
    try:
        # Store the dict representation
        redis_conn.setex(
            key,
            CACHE_TTL_SECONDS,
            response_model.model_dump_json()
        )
    except Exception as e:
         print(f"Cache Write Error: {e}")
