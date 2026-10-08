"""What one model step cost, for the run log (#1064).

Shared by the season planner and the week writer so a model or prompt change is
compared on the same numbers: tokens and dollars per attempt, with web searches
priced where the step used them.
"""

from typing import Any

from app.schemas.season import DraftAttempt
from app.services.coach import budget


def attempt_cost(model: str, usage: Any) -> DraftAttempt:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    searches = int(getattr(usage, "web_search_requests", 0) or 0)
    return DraftAttempt(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(
            budget.cost_usd(
                model, input_tokens, output_tokens, web_search_requests=searches
            ),
            4,
        ),
    )
