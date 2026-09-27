"""
db/core.py — Database Engine & Session Factory
─────────────────────────────────────────────────────────────────────────────
This file sets up THREE things every SQLAlchemy async app needs:

  1. ENGINE  — the low-level connection pool to your database.
               Think of it as the pipe between Python and the DB.

  2. SESSION FACTORY — a factory that produces individual "sessions".
               A session = one unit of work (like a shopping cart:
               add items, then checkout/rollback at the end).

  3. BASE CLASS — the base that all ORM model classes inherit from.
               SQLAlchemy uses it to discover your tables.

KEY CONCEPT — async vs sync:
  Regular (sync) SQLAlchemy blocks the whole server while waiting for the DB.
  Async SQLAlchemy uses Python's event loop, so the server can handle other
  requests while the DB query runs. This is critical for high-throughput APIs.
─────────────────────────────────────────────────────────────────────────────
"""

from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from config import settings


# ── Step 1: Create the async engine ───────────────────────────────────────────
# We inspect the DATABASE_URL to decide which engine kwargs are safe to pass.
# SQLite does NOT support connection pooling (it's a single file), so we skip
# pool_size for SQLite. PostgreSQL does support it, so we add it there.

def _make_engine():
    """
    Build the SQLAlchemy async engine with settings appropriate for the DB type.
    This function keeps our engine creation DRY and easy to understand.
    """
    is_sqlite = "sqlite" in settings.DATABASE_URL

    # Common kwargs that work for both SQLite and PostgreSQL
    kwargs = {
        "url": settings.DATABASE_URL,
        # echo=True prints every SQL statement to the terminal — great for
        # learning and debugging, but noisy. Turn off in production.
        "echo": settings.DEBUG,
    }

    # PostgreSQL supports connection pooling; SQLite does not
    if not is_sqlite:
        kwargs["pool_size"] = 10          # Max 10 persistent connections
        kwargs["pool_pre_ping"] = True    # Test connections before using them
                                          # (prevents "stale connection" errors)

    return create_async_engine(**kwargs)


engine = _make_engine()


# ── Step 2: Create the session factory ────────────────────────────────────────
# async_sessionmaker() is like a blueprint for creating sessions.
# Each request to our API will create ONE session, use it, then close it.
#
#   expire_on_commit=False → after commit(), ORM objects still have their data.
#                           Without this, accessing obj.id after commit() would
#                           trigger an extra DB query.
#   autocommit=False       → we manually call session.commit() — no surprises.
#   autoflush=False        → we control when SQL statements are sent to DB.

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Step 3: Declare the ORM Base ──────────────────────────────────────────────
# All model classes (User, Task, etc.) inherit from this Base.
# SQLAlchemy uses it to build the metadata (table names, columns, etc.)
# that init_db() uses to CREATE TABLE in the database.

class Base(DeclarativeBase):
    pass


# ── Step 4: Database initializer ──────────────────────────────────────────────
async def init_db() -> None:
    """
    Create all tables defined in ORM models if they don't already exist.

    Called once at application startup (see main.py lifespan).
    In production you'd use Alembic migrations instead of this,
    but this is perfect for learning and small projects.
    """
    # We import models here (not at the top) to avoid circular imports.
    # By importing them, Python registers them on Base.metadata so
    # create_all() knows which tables to create.
    import db.models  # noqa: F401 — imported for side-effect (registers models)

    async with engine.begin() as conn:
        # run_sync() lets us call the synchronous create_all() inside an async context
        await conn.run_sync(Base.metadata.create_all)


# ── Step 5: Dependency for FastAPI routes ─────────────────────────────────────
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI Dependency — provides a database session to route handlers.

    Usage in a route:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db_session)):
            ...

    HOW IT WORKS:
      - `yield` turns this into a "context manager generator".
      - FastAPI calls this function before the route, gets the session,
        injects it, runs the route, then continues after the `yield`
        to close/rollback the session. This guarantees cleanup even on errors.
    """
    async with async_session_maker() as session:
        try:
            yield session                    # Hand session to the route handler
        except Exception:
            await session.rollback()         # Undo any partial changes on error
            raise                            # Re-raise so FastAPI returns a 500
        finally:
            await session.close()            # Always close — returns conn to pool