import openai
import json
from typing import Optional, Dict, Any
from pydantic import ValidationError
from app.core.config import settings

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
        if not self.enabled:
             raise RuntimeError("AI service is disabled.")
             
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
        
        raise RuntimeError(f"Unsupported AI provider: {self.provider}")


# Singleton
ai_client = AIClient()
