"""#1021: the `sqlalchemy` version constraint is load-bearing, so assert it holds.

The #966 `anthropic` pin test applied to the ORM. SQLAlchemy 2.1 stopped installing
`greenlet` by default, and `alembic/env.py` imports `sqlalchemy.ext.asyncio`, which
refuses to load without it. Unbounded at `>=2.0.0`, CI resolved 2.1.1 on 29 Sep 2026
and `alembic-check` failed on every PR while `backend-test` stayed green: the suite
builds its schema with `create_all` and never imports env.py, so nothing here
exercises the import that broke. This test does not close that blindness either;
it makes the bound honest, so a venv, a CI runner and a deploy that resolve
different minors is a named failure rather than a divergence nobody notices.
"""

from tests.test_anthropic_pin_966 import _declared_specifier


def test_the_installed_sqlalchemy_matches_the_declared_constraint():
    import sqlalchemy

    spec = _declared_specifier("sqlalchemy")
    assert sqlalchemy.__version__ in spec, (
        f"installed sqlalchemy {sqlalchemy.__version__} does not satisfy the declared "
        f"'{spec}'. Reinstall the backend (pip install -e './backend[test]')."
    )


def test_the_constraint_excludes_the_minor_that_broke_migrations():
    """The bound must have a CEILING. Stated as a property of the specifier, so
    raising the floor within 2.0 does not touch this test, while widening it to
    admit 2.1 does, which is the change to make on purpose: declare `greenlet`
    (or `sqlalchemy[asyncio]`), then run the suite and a real `alembic upgrade
    head` on 2.1 before trusting it."""
    spec = _declared_specifier("sqlalchemy")

    assert "2.1.1" not in spec, (
        "the declared constraint admits sqlalchemy 2.1, which no longer installs "
        "`greenlet`, so `alembic/env.py` fails on import and migrations cannot run."
    )
