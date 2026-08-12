import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# --- CUSTOM: Import our settings and model Base ---
import importlib
import pkgutil

from app.core.config import settings
from app.db.base import Base
import app.models as _models_package

# Import every model module so `Base.metadata` carries every table (#869).
# This used to name seven models and receive the other fourteen only because
# importing `app.models` runs the barrel `__init__.py`. That made completeness
# rest on a convention nobody checked: a model in a file missing from the
# barrel would be absent from the metadata, so `--autogenerate` would not see
# its table and the `alembic check` drift guard (#839) would report no drift
# while blind to it. Walking the package removes the barrel from the path
# entirely, so the guard's reach no longer depends on an import list staying
# in sync by hand.
for _module in pkgutil.iter_modules(_models_package.__path__):
    importlib.import_module(f"{_models_package.__name__}.{_module.name}")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url with our Env var
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # We use a synchronous engine in this project for simplicity (for now)
    # mirroring session.py
    from sqlalchemy import create_engine
    
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
