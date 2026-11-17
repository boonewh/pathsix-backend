"""
Centralized logging configuration for PathSix CRM.

Provides:
- Structured logging with tenant context
- Query performance logging
- Request/response logging
- Slow query detection
"""

import logging
import time
from functools import wraps
from typing import Optional, Any, Dict
from quart import request, g
import sys

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Create logger for this module
logger = logging.getLogger('pathsix')


def get_request_context() -> Dict[str, Any]:
    """Extract relevant context from the current request."""
    context = {}
    
    try:
        if request:
            context['method'] = request.method
            context['path'] = request.path
            context['remote_addr'] = request.remote_addr
            
            # Use immutable identity; ORM users may be expired after rollback.
            principal = getattr(request, 'principal', None)
            if principal is not None:
                context['tenant_id'] = principal.tenant_id
                context['user_id'] = principal.user_id
    except RuntimeError:
        # Outside request context
        pass
    
    return context


def log_query(query_description: str, duration_ms: float, tenant_id: Optional[int] = None):
    """
    Log database query performance.
    
    Args:
        query_description: Human-readable description of the query
        duration_ms: Query execution time in milliseconds
        tenant_id: Optional tenant ID for context
    """
    context = get_request_context()
    if tenant_id:
        context['tenant_id'] = tenant_id
    
    log_data = {
        'query': query_description,
        'duration_ms': round(duration_ms, 2),
        **context
    }
    
    # Warn on slow queries (>200ms for list views, >100ms for detail views)
    if duration_ms > 200:
        logger.warning(f"Slow query detected: {log_data}")
    else:
        logger.info(f"Query executed: {log_data}")


def log_endpoint(endpoint_name: str, duration_ms: float, status_code: int = 200):
    """
    Log API endpoint performance.
    
    Args:
        endpoint_name: Name of the endpoint/route
        duration_ms: Total endpoint execution time
        status_code: HTTP response status code
    """
    context = get_request_context()
    log_data = {
        'endpoint': endpoint_name,
        'duration_ms': round(duration_ms, 2),
        'status_code': status_code,
        **context
    }
    
    level = logging.WARNING if status_code >= 400 else logging.INFO
    logger.log(level, f"Endpoint completed: {log_data}")


def log_error(error: Exception, context_message: str = ""):
    """
    Log errors with full context.
    
    Args:
        error: The exception that occurred
        context_message: Additional context about what was being attempted
    """
    context = get_request_context()
    log_data = {
        'error_type': type(error).__name__,
        'error_message': str(error),
        'context': context_message,
        **context
    }
    
    logger.error(f"Error occurred: {log_data}", exc_info=True)


def log_tenant_action(action: str, entity_type: str, entity_id: Optional[int] = None, 
                     tenant_id: Optional[int] = None, user_id: Optional[int] = None):
    """
    Log important tenant actions for audit trail.
    
    Args:
        action: Description of the action (e.g., "created", "deleted", "updated")
        entity_type: Type of entity (e.g., "client", "lead", "project")
        entity_id: Optional ID of the entity
        tenant_id: Optional tenant ID
        user_id: Optional user ID
    """
    context = get_request_context()
    
    log_data = {
        'action': action,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'tenant_id': tenant_id or context.get('tenant_id'),
        'user_id': user_id or context.get('user_id'),
    }
    
    logger.info(f"Tenant action: {log_data}")


def timing_logger(operation_name: str):
    """
    Decorator to automatically log operation timing.
    
    Usage:
        @timing_logger("fetch_clients")
        async def get_clients():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                log_endpoint(operation_name, duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                log_endpoint(operation_name, duration_ms, status_code=500)
                log_error(e, f"Error in {operation_name}")
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000
                log_endpoint(operation_name, duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                log_endpoint(operation_name, duration_ms, status_code=500)
                log_error(e, f"Error in {operation_name}")
                raise
        
        # Return appropriate wrapper based on whether function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
