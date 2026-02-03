import openai
import json
from typing import Optional, Dict, Any
from pydantic import ValidationError
from app.core.config import settings
from app.services.ai.verdict_v3.mocks import (
    MOCK_SCORECARD_JSON,
    MOCK_STORY_JSON,
    MOCK_LEVER_JSON,
    MOCK_NEXT_STEPS_JSON,
    MOCK_QUESTION_JSON
)


# Try to import OpenAI v1.x client
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

class AIClient:
    def __init__(self):
        self.enabled = settings.AI_ENABLED
        self.provider = settings.AI_PROVIDER
        
        # Initialize OpenAI v1.x client if available
        self.client = None
        if self.provider == "openai" and settings.OPENAI_API_KEY:
            if OpenAI:
                try:
                    self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
                except Exception as e:
                    print(f"Error initializing OpenAI client: {e}")
            else:
                print("Warning: 'openai' package not installed or incompatible.")


    def get_raw_json_response(self, prompt: str) -> str:
        """
        Returns raw JSON string from LLM provider without parsing.
        Used by callers who want to handle their own Pydantic validation/retries.
        """
        # If disabled OR mock provider, return intelligent mocks to ensure app works (Demo Mode)
        use_mock = (not self.enabled) or (self.provider == "mock")
        
        if use_mock:
            # Heuristic to detect which section is being requested
            # This allows the "Full Generation" flow to work end-to-end in Demo Mode
            if "VerdictScorecardResponse" in prompt:
                return MOCK_SCORECARD_JSON
            if "StoryResponse" in prompt:
                return MOCK_STORY_JSON
            if "LeverResponse" in prompt:
                return MOCK_LEVER_JSON
            if "NextStepsResponse" in prompt:
                return MOCK_NEXT_STEPS_JSON
            if "QuestionResponse" in prompt:
                return MOCK_QUESTION_JSON
            return "{}"

        if self.provider == "openai":
            if not self.client:
                 print("Error: OpenAI client not ready.")
                 raise RuntimeError("OpenAI provider configured but client not available.")

            try:
                response = self.client.chat.completions.create(
                    model="gpt-3.5-turbo-1106", 
                    messages=[
                        {"role": "system", "content": "You are a running coach. Output strict JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.5,
                    timeout=30.0 # ADDED: Prevent infinite hangs
                )
                return response.choices[0].message.content
            except Exception as e:
                print(f"OpenAI Raw API Error: {e}")
                raise e
        
        # Fallthrough
        return "{}"

# Singleton
ai_client = AIClient()
