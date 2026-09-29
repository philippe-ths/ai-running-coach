"""#928: the eval doc's assertion table must name exactly the rubric's assertions.

`docs/testing/coach-report-eval.md` documents each assertion (what it asks, when
it is NOT_APPLICABLE). It had drifted to eight rows while the rubric ran sixteen.
`ASSERTIONS` is the truth; this compares the SET of names, and refuses a table
that parses to nothing, because a guard over an empty parse passes silently.
"""

import re
from pathlib import Path

from app.services.coach.eval.rubric import ASSERTIONS

DOC = Path(__file__).resolve().parents[2] / "docs" / "testing" / "coach-report-eval.md"
_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|")


def _doc_names() -> list[str]:
    names, in_table = [], False
    for line in DOC.read_text().splitlines():
        if line.startswith("| Assertion |"):
            in_table = True
            continue
        if in_table:
            m = _ROW.match(line)
            if m:
                names.append(m.group(1))
            elif not line.startswith("|"):
                break
    return names


def _rubric_names() -> set[str]:
    return {a.__name__.removeprefix("assert_") for a in ASSERTIONS}


def test_doc_table_parses_non_empty():
    assert _doc_names(), "assertion table parsed to zero rows; the guard would be vacuous"


def test_doc_table_matches_rubric_assertions():
    doc = _doc_names()
    assert len(doc) == len(set(doc)), "duplicate row in the doc table"
    assert set(doc) == _rubric_names()
