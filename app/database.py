from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy import create_engine, event
from app.config import SQLALCHEMY_DATABASE_URI, SLOW_QUERY_THRESHOLD_MS
import os
import logging
import time

# Set up query logging
query_logger = logging.getLogger('sqlalchemy.queries')

def _log_slow_query(conn, cursor, statement, parameters, context, executemany):
    """Log slow database queries for performance monitoring."""
    duration_ms = (time.time() - context._query_start_time) * 1000
    
    if duration_ms > SLOW_QUERY_THRESHOLD_MS:
        query_logger.warning(
            f"Slow query detected ({duration_ms:.2f}ms): {statement[:200]}..."
        )

engine = create_engine(
    SQLALCHEMY_DATABASE_URI,
    echo=False,  # Don't echo all queries, we'll log slow ones only
    future=True,
    pool_pre_ping=True,     # Test connections before use, replaces stale ones
    pool_recycle=300,        # Recycle connections every 5 minutes
    pool_size=5,             # Base pool size
    max_overflow=10,         # Allow up to 15 total connections
)

# Attach query timing event listener
@event.listens_for(engine, "before_cursor_execute")
def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.time()

@event.listens_for(engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    _log_slow_query(conn, cursor, statement, parameters, context, executemany)

SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()