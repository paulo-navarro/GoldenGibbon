"""
Database setup and session management.

Uses SQLAlchemy 2.x synchronous API for database operations.
Connection pooling configured for optimal performance.
"""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ── Database URL Construction ────────────────────────────────────────────────

def get_database_url() -> str:
    """
    Construct database URL from environment variables.
    
    Returns:
        str: PostgreSQL connection URL
    """
    user = os.getenv("POSTGRES_USER", "trade")
    password = os.getenv("POSTGRES_PASSWORD", "trade_dev")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "trade")
    
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


# ── SQLAlchemy Engine ────────────────────────────────────────────────────────

# Create engine with connection pooling
# pool_pre_ping ensures connections are alive before using them
# echo=True for development (logs all SQL), set False for production
engine = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)


# ── Declarative Base ─────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """
    Base class for all ORM models.
    
    All models should inherit from this class to be tracked by SQLAlchemy.
    """
    pass


# ── Session Factory ──────────────────────────────────────────────────────────

# Create session factory
# autoflush=False: Manual control over when to flush changes
# autocommit=False: Require explicit commit (safer)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


# ── Session Management ───────────────────────────────────────────────────────

@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.
    
    Automatically handles commit/rollback and cleanup.
    
    Usage:
        with get_session() as session:
            candle = session.query(CandleRecord).first()
    
    Yields:
        Session: Database session
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Advisory lock ID for state_data writes (reconciliation + balance sync).
_STATE_DATA_LOCK_ID = 827_400_001


@contextmanager
def state_data_lock(session: Session) -> Generator[None, None, None]:
    """Acquire a Postgres advisory lock scoped to the session (released on commit/rollback)."""
    session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _STATE_DATA_LOCK_ID})
    yield


def get_db() -> Generator[Session, None, None]:
    """
    Dependency injection for FastAPI.
    
    Usage in FastAPI:
        @app.get("/candles")
        def get_candles(db: Session = Depends(get_db)):
            return db.query(CandleRecord).all()
    
    Yields:
        Session: Database session
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── Database Utilities ───────────────────────────────────────────────────────

def init_db() -> None:
    """
    Initialize database tables.
    
    Creates all tables defined in models if they don't exist.
    For production, use Alembic migrations instead.
    """
    # Import models to register them with Base
    from db import models  # noqa: F401
    
    Base.metadata.create_all(bind=engine)


def drop_all_tables() -> None:
    """
    Drop all database tables.
    
    WARNING: This will delete all data!
    Use only for testing or during development.
    """
    Base.metadata.drop_all(bind=engine)


def check_connection() -> bool:
    """
    Check if database connection is working.
    
    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False


# ── Exports ──────────────────────────────────────────────────────────────────

__all__ = [
    "Base",
    "engine",
    "SessionLocal",
    "get_session",
    "get_db",
    "init_db",
    "drop_all_tables",
    "check_connection",
    "get_database_url",
]
