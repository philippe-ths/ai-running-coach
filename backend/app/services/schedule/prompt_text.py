"""Season free text, made safe to put in a prompt (#1064).

The coach writes a season's summary, each goal's success and approach and each
phase's focus, and that text is later read back into other prompts (the weeks
writer, the chat coach). A model-written string with a line break can forge a
line there ("## THE WEEKS", "Ignore every limit above"), so wherever season text
is interpolated into ANY prompt it goes through `flat`: one line, single spaces,
bounded. Screens show the stored text as it is (React renders it as plain text).
"""

# The schema's own caps (`app/schemas/season.py`), repeated here because stored
# rows can pre-date a tightened cap and the prompt must not trust the column.
SUMMARY_MAX = 1500
SUCCESS_MAX = 300
APPROACH_MAX = 800
FOCUS_MAX = 200


def flat(value: object, limit: int) -> str:
    """`value` on one line: control characters dropped, every run of whitespace
    (line breaks, tabs, Unicode separators) a single space, cut to `limit`."""
    text = "".join(ch for ch in str(value or "") if ch.isprintable() or ch.isspace())
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: max(0, limit - 3)].rstrip() + "..."
    return text
