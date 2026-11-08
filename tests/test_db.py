from sqlalchemy import text

from d2_trader.db import make_engine, make_sessions


async def test_engine_and_sessions_reach_the_database(database_url):
    engine = make_engine(database_url)
    async with make_sessions(engine)() as session:
        assert (await session.execute(text("select 1"))).scalar_one() == 1
    await engine.dispose()
