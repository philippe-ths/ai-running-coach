import json
from typing import Type, TypeVar, Dict, Any, Optional, Protocol

from pydantic import BaseModel, ValidationError

from app.schemas import (
    VerdictScorecardResponse,
    StoryResponse,
    LeverResponse,
    NextStepsResponse,
    QuestionResponse,
    CoachVerdictV3
)
from app.services.coaching.v3.slicers import (
    slice_for_scorecard,
    slice_for_story,
    slice_for_lever,
    slice_for_next_steps,
    slice_for_question
)
from app.services.ai.verdict_v3.prompts import (
    build_scorecard_prompt,
    build_story_prompt,
    build_lever_prompt,
    build_next_steps_prompt,
    build_question_prompt
)
from app.services.ai.verdict_v3.safety import enforce_scorecard_safety, enforce_next_steps_safety
from app.services.ai.verdict_v3.cache import get_cached_section, set_cached_section
from app.services.ai.client import ai_client

T = TypeVar("T", bound=BaseModel)

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
    # 0. Check Usage of Cache
    cached = get_cached_section("scorecard", context_pack, VerdictScorecardResponse)
    if cached: return cached

    slice_data = slice_for_scorecard(context_pack)
    prompt = build_scorecard_prompt(slice_data)
    result = _generate_section("scorecard", prompt, VerdictScorecardResponse, client)
    
    # Cache Result
    set_cached_section("scorecard", context_pack, result)
    return result

def generate_story(context_pack: Dict[str, Any], client: ClientInterface = ai_client) -> StoryResponse:
    cached = get_cached_section("story", context_pack, StoryResponse)
    if cached: return cached

    slice_data = slice_for_story(context_pack)
    prompt = build_story_prompt(slice_data)
    result = _generate_section("story", prompt, StoryResponse, client)
    
    set_cached_section("story", context_pack, result)
    return result

def generate_lever(
    context_pack: Dict[str, Any], 
    scorecard: VerdictScorecardResponse, 
    client: ClientInterface = ai_client
) -> LeverResponse:
    # Composite key for dependency
    cache_input = {"cp": context_pack, "sc": scorecard.model_dump()}
    cached = get_cached_section("lever", cache_input, LeverResponse)
    if cached: return cached

    slice_data = slice_for_lever(context_pack, scorecard)
    prompt = build_lever_prompt(slice_data)
    result = _generate_section("lever", prompt, LeverResponse, client)
    
    set_cached_section("lever", cache_input, result)
    return result

def generate_next_steps(
    context_pack: Dict[str, Any], 
    scorecard: VerdictScorecardResponse, 
    lever: LeverResponse, 
    client: ClientInterface = ai_client
) -> NextStepsResponse:
    cache_input = {"cp": context_pack, "sc": scorecard.model_dump(), "lv": lever.model_dump()}
    cached = get_cached_section("next_steps", cache_input, NextStepsResponse)
    if cached: return cached

    slice_data = slice_for_next_steps(context_pack, scorecard, lever)
    prompt = build_next_steps_prompt(slice_data)
    result = _generate_section("next_steps", prompt, NextStepsResponse, client)

    set_cached_section("next_steps", cache_input, result)
    return result

def generate_question(
    context_pack: Dict[str, Any], 
    scorecard: VerdictScorecardResponse, 
    client: ClientInterface = ai_client
) -> QuestionResponse:
    cache_input = {"cp": context_pack, "sc": scorecard.model_dump()}
    cached = get_cached_section("question", cache_input, QuestionResponse)
    if cached: return cached

    slice_data = slice_for_question(context_pack, scorecard)
    prompt = build_question_prompt(slice_data)
    result = _generate_section("question", prompt, QuestionResponse, client)

    set_cached_section("question", cache_input, result)
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

    # Step 6: Merge
    return CoachVerdictV3(
        inputs_used_line=scorecard.inputs_used_line,
        headline=scorecard.headline,
        why_it_matters=scorecard.why_it_matters,
        scorecard=scorecard.scorecard,
        run_story=story.run_story,
        lever=lever.lever,
        next_steps=next_steps.next_steps,
        question_for_you=question.question_for_you
    )

