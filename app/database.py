from sqlalchemy.orm import Session, sessionmaker, declarative_base, with_loader_criteria
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
    pool_use_lifo=True,      # Prefer recently verified connections during quiet periods
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

# Quart serves concurrent requests as async tasks on the same thread. A default
# scoped_session is thread-local, so separate requests can accidentally receive
# the same Session. Always return a fresh Session instead.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


@event.listens_for(Session, 'after_begin')
def initialize_database_identity(session, transaction, connection):
    from app.services.database_context import apply_context
    apply_context(session, connection)


@event.listens_for(Session, "before_flush")
@event.listens_for(Session, "before_commit")
def mark_request_write(session, *args):
    from quart import has_request_context, request
    if has_request_context():
        request.database_write_started = True


@event.listens_for(Session, "do_orm_execute")
def scope_authenticated_queries(state):
    """Also scope relationship loads and joins, including legacy malformed rows.

    Background operations still require explicit tenant predicates. This is an ORM
    defense in depth for authenticated HTTP requests, not database row security.
    """
    from quart import has_request_context, request
    if not has_request_context() or not hasattr(request, "principal"):
        return
    if state.is_insert or state.is_update or state.is_delete:
        request.database_write_started = True
    tenant_id = request.principal.tenant_id
    for mapper in Base.registry.mappers:
        model = mapper.class_
        if hasattr(model, "tenant_id"):
            state.statement = state.statement.options(with_loader_criteria(
                model, model.tenant_id == tenant_id, include_aliases=True,
            ))
