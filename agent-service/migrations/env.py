from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import revenueops.cases.models  # noqa: F401  (registers the tables on Base.metadata)
import revenueops.executor.models  # noqa: F401
from revenueops.config import get_settings
from revenueops.db import Base

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    # A URL set on the Alembic config (tests) wins over DATABASE_URL.
    return context.config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
