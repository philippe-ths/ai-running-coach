"""What one model step cost, for the run log (#1064).

Shared by the season planner and the week writer so a model or prompt change is
compared on the same numbers: tokens and dollars per attempt, with web searches
priced where the step used them.
"""

from typing import Any

from app.schemas.season import DraftAttempt
from app.services.coach import budget
from app.services.coach.llm import ReasonedCallFailed


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


def output_failure(exc: ReasonedCallFailed, tool_name: str) -> str:
    """What to tell the coach on its rewrite when its call ran but gave no answer.

    The call was billed and the fault was its output, so this is a rewrite
    prompt's business (one retry, logged with its cost) and never a "could not
    be reached" to the runner.
    """
    if exc.truncated:
        return (
            f"Your previous answer was cut off before {tool_name} finished, so none "
            "of it could be read. Answer again with less free text: shorter "
            "sentences, and no field longer than it needs to be."
        )
    return (
        f"You did not call {tool_name}, so there was no answer to read. Answer only "
        f"by calling {tool_name}."
    )
