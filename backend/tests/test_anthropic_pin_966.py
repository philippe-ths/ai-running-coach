"""#966: the `anthropic` version constraint is load-bearing, so assert it holds.

The #809 `test_the_installed_fastapi_matches_the_declared_constraint` pattern,
applied to the other dependency whose surface we call directly.

Why this dependency earned a bound: `anthropic` 1.0.0 removed `temperature` /
`top_p` / `top_k` from `messages.create()` and `messages.stream()`, and neither
takes `**kwargs`, so the three call sites in `services/coach/llm.py` that pass one
raise TypeError. Unbounded at `>=0.40.0`, the 24 Aug 2026 image build resolved
1.0.0 and every conversational turn, period report, schedule draft, voice rewrite,
memory update, material distillation and receipt-voice pass failed in production
while CI stayed green, because the suite mocks the client end to end.

This test does NOT close that blindness -- it cannot, since it reads a declared
string rather than binding call sites to the SDK's real signature. What it does is
make the bound honest: a venv, a CI runner and a deploy that resolve different
majors is a named failure here rather than a divergence nobody notices.
"""


def _declared_specifier(name: str):
    import tomllib
    from pathlib import Path

    from packaging.requirements import Requirement

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    declared = [
        Requirement(dep)
        for dep in tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    ]
    (spec,) = [r.specifier for r in declared if r.name == name]
    return spec


def test_the_installed_anthropic_matches_the_declared_constraint():
    import anthropic

    spec = _declared_specifier("anthropic")
    assert str(spec), "anthropic must carry an explicit version constraint (#966)"
    assert anthropic.__version__ in spec, (
        f"installed anthropic {anthropic.__version__} does not satisfy the declared "
        f"'{spec}'. Reinstall the backend (pip install -e './backend[test]'); a venv "
        "on a different SDK major calls a different parameter surface than CI does."
    )


def test_the_constraint_excludes_the_major_that_broke_production():
    """The bound must have a CEILING, not just a floor.

    A floor alone is what `>=0.40.0` already was, and it is precisely what let
    1.0.0 in. Stated as a property of the specifier rather than as a literal
    string, so raising the floor within 0.x does not touch this test, while
    widening it to admit 1.x does -- which is the change that must be made on
    purpose, together with the two call-site fixes the upgrade needs.
    """
    spec = _declared_specifier("anthropic")

    assert "1.0.0" not in spec, (
        "the declared constraint admits anthropic 1.0.0, which removed the sampling "
        "parameters `services/coach/llm.py` passes. Adopting 1.x is a deliberate "
        "change: move `temperature` into `extra_body` (or drop it) at the three "
        "call sites, and re-point the `httpx.RemoteProtocolError` catch in "
        "`RetryLadder` at `httpx2`, whose exception classes are unrelated."
    )
