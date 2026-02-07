import json
from typing import Type, TypeVar, Dict, Any, Optional, Protocol

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.schemas import (
    VerdictScorecardResponse,
    StoryResponse,
    LeverResponse,
    NextStepsResponse,
    QuestionResponse,
    CoachVerdictV3,
    SummaryResponse
)
from app.services.coaching.v3.slicers import (
    slice_for_scorecard,
    slice_for_story,
    slice_for_lever,
    slice_for_next_steps,
    slice_for_question,
    slice_for_summary
)
from app.services.ai.verdict_v3.prompts import (
    build_scorecard_prompt,
    build_story_prompt,
    build_lever_prompt,
    build_next_steps_prompt,
    build_question_prompt,
    build_summary_prompt
)
from app.services.ai.verdict_v3.safety import enforce_scorecard_safety, enforce_next_steps_safety
from app.services.ai.verdict_v3.cache import get_cached_section, set_cached_section
from app.services.ai.client import ai_client

T = TypeVar("T", bound=BaseModel)


def _attach_debug(result: BaseModel, context: Any, prompt: Dict[str, str]) -> None:
    """Set debug_context/debug_prompt only when DEBUG_AI is enabled."""
    if settings.DEBUG_AI:
        result.debug_context = context
        result.debug_prompt = prompt
    else:
        result.debug_context = None
        result.debug_prompt = None

class ClientInterface(Protocol):
    def get_raw_json_response(self, prompt: str) -> str: ...

class VerdictV3GenerationError(Exception):
    def __init__(self, section: str, message: str, original_error: Exception = None):
        super().__init__(f"Failed to generate {section}: {message}")
        self.section = section
        self.message = message
        self.original_error = original_error

def _generate_section(
    section_name: str,
    prompt: str,
    schema: Type[T],
    client: ClientInterface
) -> T:
    """
    Generic handler for generating a V3 section.
    Handles calls, validation, and one-shot repair retry.
    """
    
    # 1. First Attempt
    raw_response = ""
    try:
        raw_response = client.get_raw_json_response(prompt)
        data = json.loads(raw_response)
        return schema(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"[{section_name}] Generation failed: {e}. Attempting repair...")
        
        # 2. Repair Attempt
        repair_prompt = (
            f"{prompt}\n\n"
            f"[SYSTEM ERROR]: The previous response was invalid.\n"
            f"Error: {str(e)}\n"
            f"Received Invalid JSON:\n{raw_response}\n\n"
            f"CORRECT THE JSON FORMAT AND RETURN ONLY THE FIXED JSON."
        )
        
        try:
            raw_response_2 = client.get_raw_json_response(repair_prompt)
            data_2 = json.loads(raw_response_2)
            return schema(**data_2)
        except (json.JSONDecodeError, ValidationError) as e2:
            print(f"[{section_name}] Repair failed: {e2}")
            raise VerdictV3GenerationError(
                section=section_name, 
                message=f"Validation failed after retry: {str(e2)}",
                original_error=e2
            )
    except Exception as e:
        raise VerdictV3GenerationError(
            section=section_name,
            message=f"Unexpected error: {str(e)}",
            original_error=e
        )

def generate_scorecard(context_pack: Dict[str, Any], client: ClientInterface = ai_client) -> VerdictScorecardResponse:
    # Always build prompt for debug visibility (cheap operation)
    slice_data = slice_for_scorecard(context_pack)
    prompt = build_scorecard_prompt(slice_data)

    # 0. Check Usage of Cache
    cached = get_cached_section("scorecard", context_pack, VerdictScorecardResponse)
    if cached: 
        _attach_debug(cached, slice_data, {"scorecard": prompt, "why_it_matters": prompt})
        return cached

    result = _generate_section("scorecard", prompt, VerdictScorecardResponse, client)
    
    # Enrich with Debug Info
    _attach_debug(result, slice_data, {"scorecard": prompt, "why_it_matters": prompt})

    # Cache Result
    set_cached_section("scorecard", context_pack, result)
    return result

def generate_story(context_pack: Dict[str, Any], client: ClientInterface = ai_client) -> StoryResponse:
    slice_data = slice_for_story(context_pack)
    prompt = build_story_prompt(slice_data)

    cached = get_cached_section("story", context_pack, StoryResponse)
    if cached: 
        _attach_debug(cached, slice_data, {"story": prompt})
        return cached

    result = _generate_section("story", prompt, StoryResponse, client)
    
    # Enrich with Debug Info
    _attach_debug(result, slice_data, {"story": prompt})

    set_cached_section("story", context_pack, result)
    return result

def generate_lever(
    context_pack: Dict[str, Any], 
    scorecard: VerdictScorecardResponse, 
    client: ClientInterface = ai_client
) -> LeverResponse:
    slice_data = slice_for_lever(context_pack, scorecard)
    prompt = build_lever_prompt(slice_data)

    # Composite key for dependency
    cache_input = {"cp": context_pack, "sc": scorecard.model_dump()}
    cached = get_cached_section("lever", cache_input, LeverResponse)
    if cached: 
        _attach_debug(cached, slice_data, {"lever": prompt})
        return cached

    result = _generate_section("lever", prompt, LeverResponse, client)
    
    # Enrich with Debug Info
    _attach_debug(result, slice_data, {"lever": prompt})

    set_cached_section("lever", cache_input, result)
    return result

def generate_next_steps(
    context_pack: Dict[str, Any], 
    scorecard: VerdictScorecardResponse, 
    lever: LeverResponse, 
    client: ClientInterface = ai_client
) -> NextStepsResponse:
    slice_data = slice_for_next_steps(context_pack, scorecard, lever)
    prompt = build_next_steps_prompt(slice_data)

    cache_input = {"cp": context_pack, "sc": scorecard.model_dump(), "lv": lever.model_dump()}
    cached = get_cached_section("next_steps", cache_input, NextStepsResponse)
    if cached: 
        _attach_debug(cached, slice_data, {"next_steps": prompt})
        return cached

    result = _generate_section("next_steps", prompt, NextStepsResponse, client)

    # Enrich with Debug Info
    _attach_debug(result, slice_data, {"next_steps": prompt})

    set_cached_section("next_steps", cache_input, result)
    return result

def generate_question(
    context_pack: Dict[str, Any], 
    scorecard: VerdictScorecardResponse, 
    client: ClientInterface = ai_client
) -> QuestionResponse:
    slice_data = slice_for_question(context_pack, scorecard)
    prompt = build_question_prompt(slice_data)

    cache_input = {"cp": context_pack, "sc": scorecard.model_dump()}
    cached = get_cached_section("question", cache_input, QuestionResponse)
    if cached: 
        _attach_debug(cached, slice_data, {"question": prompt})
        return cached

    result = _generate_section("question", prompt, QuestionResponse, client)

    # Enrich with Debug Info
    _attach_debug(result, slice_data, {"question": prompt})

    set_cached_section("question", cache_input, result)
    return result

def generate_executive_summary(
    context_pack: Dict[str, Any],
    scorecard: VerdictScorecardResponse,
    lever: LeverResponse,
    story: StoryResponse,
    next_steps: NextStepsResponse,
    client: ClientInterface = ai_client
) -> SummaryResponse:
    slice_data = slice_for_summary(context_pack, scorecard, lever, story, next_steps)
    prompt = build_summary_prompt(slice_data)

    # Cache key includes everything? Might be too large.
    # Just hash the generated components + CP.
    # Pydantic model_dump can function as unique input.
    cache_input = {
        "cp": context_pack,
        "sc": scorecard.model_dump(),
        "lv": lever.model_dump(),
        "st": story.model_dump(),
        "ns": next_steps.model_dump()
    }
    cached = get_cached_section("summary", cache_input, SummaryResponse)
    if cached: 
        _attach_debug(cached, slice_data, {"summary": prompt})
        return cached

    result = _generate_section("summary", prompt, SummaryResponse, client)
    
    # Enrich with Debug Info
    _attach_debug(result, slice_data, {"summary": prompt})

    set_cached_section("summary", cache_input, result)
    return result

def generate_full_verdict_orchestrator(
    context_pack: Dict[str, Any],
    client: ClientInterface = ai_client
) -> CoachVerdictV3:
    """
    Orchestrates the full generation of CoachVerdictV3 purely in service layer.
    """
    # Step 1: Scorecard & Safety
    scorecard = generate_scorecard(context_pack, client)
    scorecard = enforce_scorecard_safety(scorecard, context_pack)

    # Step 2: Story
    story = generate_story(context_pack, client)

    # Step 3: Lever
    lever = generate_lever(context_pack, scorecard, client)

    # Step 4: Next Steps & Safety
    next_steps = generate_next_steps(context_pack, scorecard, lever, client)
    next_steps = enforce_next_steps_safety(next_steps, scorecard, context_pack)

    # Step 5: Question
    question = generate_question(context_pack, scorecard, client)
    
    # Step 6: Summary / Verdict
    summary = generate_executive_summary(context_pack, scorecard, lever, story, next_steps, client)

    # Step 7: Debug Info Construction
    # We reconstruct prompts for developer visibility (safe to re-run deterministic builders)
    scorecard_prompt = build_scorecard_prompt(slice_for_scorecard(context_pack))
    debug_prompts = {
        "scorecard": scorecard_prompt,
        "why_it_matters": scorecard_prompt,
        "story": build_story_prompt(slice_for_story(context_pack)),
        "lever": build_lever_prompt(slice_for_lever(context_pack, scorecard)),
        "next_steps": build_next_steps_prompt(slice_for_next_steps(context_pack, scorecard, lever)),
        "question": build_question_prompt(slice_for_question(context_pack, scorecard)),
        "summary": build_summary_prompt(slice_for_summary(context_pack, scorecard, lever, story, next_steps))
    }

    # Step 8: Merge
    return CoachVerdictV3(
        inputs_used_line=scorecard.inputs_used_line,
        executive_summary=summary.executive_summary,
        headline=None, # Deprecated
        why_it_matters=scorecard.why_it_matters,
        scorecard=scorecard.scorecard,
        run_story=story.run_story,
        lever=lever.lever,
        next_steps=next_steps.next_steps,
        question_for_you=question.question_for_you,
        debug_context=context_pack if settings.DEBUG_AI else None,
        debug_prompt=debug_prompts if settings.DEBUG_AI else None
    )

