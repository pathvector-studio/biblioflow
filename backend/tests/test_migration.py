from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db.base import Base
import app.models  # noqa: F401
from app.db.session import get_engine


async def test_migration_matches_models(infra):
    def _diff(sync_conn):
        mc = MigrationContext.configure(sync_conn, opts={"compare_type": True, "compare_server_default": False})
        return compare_metadata(mc, Base.metadata)

    async with get_engine().connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == [], diff
