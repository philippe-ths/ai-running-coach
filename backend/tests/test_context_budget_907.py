"""#907: `project-context.md` has a budget, and the build enforces it.

The file is `@`-imported by `CLAUDE.md`, so every line of it loads into every
session before any work begins. It had grown to 187,923 chars — roughly a
quarter of a 200k window, and 95% of all always-resident memory in this repo.

A first trim (#908) cut it to 171,954. Seven days and five feature commits
later it was 187,923 again. The growth mechanism is visible in those diffs:
each one is `1 add / 1 del` or `5 add / 5 del`, the signature of appending a
clause to a line that already exists rather than adding a line. Between the
trim and the regrowth the file gained 15,969 chars while gaining 7 lines, and
chars-per-line went 834 -> 882.

That is the failure this file guards. The owning skill
(`aiw-project-context-management`) states two rules that trade against each
other: "keep the file under 300 lines" and "write exactly one sentence per
line". Only the first is checkable at a glance, so agents honoured it by
fusing sentences — which is why the line count stayed flat while the character
count tripled. `MAX_LINE_CHARS` is the rule that had no teeth: it is what makes
fusing fail instead of succeed.

The budgets carry deliberate headroom over the trimmed file so a genuinely new
subsystem can land without forcing an immediate trim. When one is hit, the fix
is to drop low-value detail, not to raise the number — the skill's own rule is
"if the file approaches 300 lines, drop low-value detail before adding new
content". Raising a budget here should be a decision someone argues for, which
is why it costs a code change.
"""

from pathlib import Path

_PROJECT_CONTEXT = Path(__file__).resolve().parents[2] / "project-context.md"

# The owning skill's stated cap.
MAX_LINES = 300
# Headroom over the trimmed file (45,992 chars at the time of writing).
MAX_CHARS = 55_000
# The anti-fusing rule. The longest legitimate line is the pack-section list at
# 492 chars; anything past this is several facts wearing one line as a costume.
MAX_LINE_CHARS = 600


def _text() -> str:
    return _PROJECT_CONTEXT.read_text()


def test_the_file_stays_under_the_line_cap():
    lines = _text().splitlines()
    assert len(lines) <= MAX_LINES, (
        f"project-context.md is {len(lines)} lines, over the {MAX_LINES}-line "
        "cap its owning skill sets. Drop low-value detail rather than raising "
        "the cap: every line here loads into every session in this repo."
    )


def test_the_file_stays_under_the_character_budget():
    text = _text()
    assert len(text) <= MAX_CHARS, (
        f"project-context.md is {len(text)} chars, over its {MAX_CHARS} budget "
        f"(~{len(text) // 4}k tokens loaded into every session). The line cap "
        "alone never caught this, because the file tripled in size while the "
        "line count stayed flat."
    )


def test_no_single_line_carries_a_paragraph():
    """The rule with no teeth, given teeth.

    Fusing a new fact onto an existing line is how the file grew 16k chars in a
    week while gaining 7 lines. A line over this length is the thing the
    one-sentence-per-line rule exists to forbid, and it is the one violation a
    reviewer cannot see in a diff — the line is already long, so a longer one
    looks the same.
    """
    long_lines = [
        (n, len(line))
        for n, line in enumerate(_text().splitlines(), 1)
        if len(line) > MAX_LINE_CHARS
    ]
    assert not long_lines, (
        f"project-context.md has lines over {MAX_LINE_CHARS} chars: {long_lines}. "
        "Write exactly one sentence per line and add a new line for a new fact; "
        "do not grow an existing line to carry one."
    )
