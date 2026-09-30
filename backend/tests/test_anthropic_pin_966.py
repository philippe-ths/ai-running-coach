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

This test does NOT close that blindness -- it reads a declared string, and
`test_anthropic_sdk_binding_1023.py` is what binds the call sites to the SDK's
real signature. What this one does is make the bound honest: a venv, a CI
runner and a deploy that resolve different majors is a named failure here rather
than a divergence nobody notices.
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


def test_the_constraint_excludes_the_next_major():
    """The bound must have a CEILING, not just a floor.

    A floor alone is what `>=0.40.0` was, and it is precisely what let 1.0.0 in
    unannounced. Stated relative to the INSTALLED major rather than as a literal,
    so it holds across a floor raise and needs no edit when a later major is
    adopted on purpose (#1023 took 1.x); it fails only when the ceiling is gone.
    """
    import anthropic

    spec = _declared_specifier("anthropic")
    next_major = f"{int(anthropic.__version__.split('.')[0]) + 1}.0.0"

    assert next_major not in spec, (
        f"the declared constraint admits anthropic {next_major}. A new SDK major "
        "can change the call signatures `services/coach/llm.py` binds to (1.0.0 "
        "removed `temperature`, #966); adopt it on purpose, with "
        "`test_anthropic_sdk_binding_1023.py` green against it."
    )
