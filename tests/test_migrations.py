from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from d2_trader.models import Base


def test_migrations_build_exactly_what_the_models_describe(database_url):
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()
