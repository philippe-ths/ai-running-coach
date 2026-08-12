"""#869: `Base.metadata` must carry every model, or the drift guard goes blind.

`alembic check` (the CI job #839 added) answers "do the models and the
migrations agree" by diffing the live database against `Base.metadata`. That
answer is only worth what the metadata's completeness is worth: a model whose
module never gets imported has no table in the metadata, so `--autogenerate`
emits nothing for it and `check` reports no drift. The guard passes while
looking at less than the whole schema.

`alembic/env.py` used to name seven models and receive the other fourteen only
as a side effect of the barrel `__init__.py` running. These tests pin the two
properties that replaced that convention.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import app.models as models_package
from app.db.base import Base

MODELS_DIR = Path(models_package.__file__).parent


def _module_names_on_disk() -> set[str]:
    """Every model module as the filesystem has it, the barrel not consulted."""
    return {
        path.stem
        for path in MODELS_DIR.glob("*.py")
        if path.stem != "__init__"
    }


def test_package_walk_sees_every_model_module_on_disk():
    """The mechanism `env.py` relies on must find every module.

    `env.py` imports models by walking the package with `pkgutil`. If that walk
    ever saw less than the directory holds, the metadata would be incomplete
    again and nothing else here would notice.
    """
    walked = {module.name for module in pkgutil.iter_modules(models_package.__path__)}

    assert walked == _module_names_on_disk()


def test_every_model_class_has_its_table_in_the_metadata():
    """Import every module the way `env.py` does; every mapped class registers.

    This is the property `alembic check` actually depends on. A model that
    imports but fails to register (a missing `__tablename__`, a class that never
    reached the declarative base) would be invisible to autogenerate.
    """
    unregistered: list[str] = []

    for module_info in pkgutil.iter_modules(models_package.__path__):
        module = importlib.import_module(
            f"{models_package.__name__}.{module_info.name}"
        )
        for attribute_name in dir(module):
            attribute = getattr(module, attribute_name)
            if not isinstance(attribute, type) or not issubclass(attribute, Base):
                continue
            if attribute is Base:
                continue
            table_name = getattr(attribute, "__tablename__", None)
            # A class declared in one module and imported into another (a
            # relationship's target, say) shows up under both; the table is what
            # matters, so the same name simply passes twice.
            if table_name is None or table_name not in Base.metadata.tables:
                unregistered.append(f"{module_info.name}.{attribute_name}")

    assert unregistered == []


def test_the_barrel_still_exports_every_model_module():
    """The barrel is the documented index, so it should stay honest.

    `env.py` no longer depends on this (it walks the package), which is exactly
    why the barrel could now drift unnoticed: nothing else would break. The
    barrel is what application code imports from, so a module missing here is
    still a real defect, just a quieter one than it used to be.
    """
    barrel_source = (MODELS_DIR / "__init__.py").read_text(encoding="utf-8")

    imported: set[str] = set()
    for node in ast.walk(ast.parse(barrel_source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            prefix = f"{models_package.__name__}."
            if node.module.startswith(prefix):
                imported.add(node.module[len(prefix) :])

    missing = _module_names_on_disk() - imported
    assert missing == set(), (
        f"model modules not re-exported by app/models/__init__.py: {sorted(missing)}"
    )
