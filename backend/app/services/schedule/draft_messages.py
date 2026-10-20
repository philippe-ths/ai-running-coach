"""What the runner is told when a plan draft fails (#859, #1048).

Read in two places: the draft status the Schedule screen and the coach sheet
poll, and the coach's note in the conversation a failed draft was confirmed in.
One copy, so the two cannot tell the runner different things about one failure.
"""

from typing import Optional

from app.services.schedule import store

# One sentence per failure CATEGORY (#859). A draft can fail for reasons the
# runner has genuinely different moves for, and until now all of them arrived as
# the same sentence: someone whose coach had ramped too hard was told to "ask
# again", which produces the same plan and the same rejection.
#
# What each sentence is held to: it says what happened in the runner's own terms,
# it names the move available to them, it states that nothing they had has
# changed, and it carries none of the gate's machinery. The category is chosen in
# `draft.py` where the failure is known; nothing internal ever travels here.
DRAFT_FAILURE_MESSAGES = {
    store.FAILURE_TOO_BIG_A_JUMP: (
        "Your coach wrote a block that climbs much faster than your recent weeks, "
        "so it was not saved. Nothing has changed — ask again for a gentler "
        "build, or talk it through and have the same block spread over more weeks."
    ),
    store.FAILURE_UNREACHABLE: (
        "Your coach could not be reached, so no plan was written. Nothing has "
        "changed — try again in a few minutes."
    ),
    store.FAILURE_OVER_BUDGET: (
        "You have used this period's coaching allowance, so no plan was written. "
        "Nothing has changed — your runs still sync and your reports still "
        "arrive. Ask again once the allowance resets."
    ),
    store.FAILURE_SEASON: (
        "Your coach could not settle a season that held together, so no plan was "
        "written. Nothing has changed: ask again, or talk it through in a "
        "conversation."
    ),
    # The fallback, and the sentence every failure used to get. No "just now"
    # (#879). This is read at the moment of failure and for as long afterwards as
    # the runner has not asked for another plan, which can be days: only the
    # empty-week panel used to show it, and only to someone sitting there
    # watching. Now it is on the Schedule screen of anyone whose last attempt
    # failed, so it has to stay true when it is no longer news.
    store.FAILURE_UNKNOWN: (
        "Your coach could not write a plan. Nothing has changed — ask again, or "
        "talk it through in a conversation."
    ),
}


def failure_message(kind: Optional[str]) -> str:
    """The sentence for THIS failure. Falls back for a row with no category —
    every plan that failed before the column existed, and any category a future
    writer adds without a sentence to go with it."""
    return DRAFT_FAILURE_MESSAGES.get(
        kind or store.FAILURE_UNKNOWN,
        DRAFT_FAILURE_MESSAGES[store.FAILURE_UNKNOWN],
    )
