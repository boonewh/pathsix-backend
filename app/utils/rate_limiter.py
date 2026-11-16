"""
Simple in-memory rate limiter for authentication endpoints.

For production with multiple server instances, consider Redis-backed rate limiting.
This implementation uses a sliding window counter approach.
"""
import ipaddress
import os
import time
from collections import defaultdict
from functools import wraps
from quart import request, jsonify

# In-memory storage: {(endpoint, ip_address): [(timestamp, count), ...]}
_rate_limit_store = defaultdict(list)

# Cleanup old entries every N requests
_cleanup_counter = 0
_cleanup_threshold = 100


def _get_client_ip():
    """Identify the visitor without trusting client-controlled headers.

    On Fly, every request arrives from Fly's proxy, so the socket peer is the
    same proxy address for everyone and would put all visitors in one bucket.
    Fly's proxy sets Fly-Client-IP to the address it accepted the connection
    from, replacing any value a client sends. X-Forwarded-For and similar
    headers stay untrusted. Off Fly, the socket peer is used as before.
    """
    return client_ip(request.headers, request.remote_addr)


def client_ip(headers, remote_addr):
    if os.getenv('FLY_APP_NAME'):
        # Quart headers are case-insensitive multi-dicts; the MCP gateway passes
        # a plain dict with lowercase names and refuses duplicates itself.
        values = (headers.getlist('Fly-Client-IP') if hasattr(headers, 'getlist')
                  else [headers.get('fly-client-ip')])
        if len(values) == 1 and values[0]:
            try:
                return str(ipaddress.ip_address(values[0].strip()))
            except ValueError:
                pass
    return remote_addr or 'unknown'


def _cleanup_old_entries():
    """Remove expired entries to prevent memory bloat."""
    global _cleanup_counter
    _cleanup_counter += 1
    
    if _cleanup_counter >= _cleanup_threshold:
        current_time = time.time()
        expired_keys = []
        
        for key, attempts in _rate_limit_store.items():
            # Remove attempts older than 1 hour
            _rate_limit_store[key] = [
                (timestamp, count) for timestamp, count in attempts
                if current_time - timestamp < 3600
            ]
            # Mark empty endpoint/IP buckets for deletion
            if not _rate_limit_store[key]:
                expired_keys.append(key)
        
        for key in expired_keys:
            del _rate_limit_store[key]
        
        _cleanup_counter = 0


def _valid_attempts(bucket_key, window_seconds, current_time):
    cutoff_time = current_time - window_seconds
    return [(timestamp, count) for timestamp, count in _rate_limit_store.get(bucket_key, [])
            if timestamp > cutoff_time]


def retry_after(bucket_key, max_attempts: int, window_seconds: int):
    """Seconds until bucket_key may try again, or None while under the limit."""
    current_time = time.time()
    valid_attempts = _valid_attempts(bucket_key, window_seconds, current_time)
    if sum(count for _, count in valid_attempts) < max_attempts:
        return None
    oldest_timestamp = min(timestamp for timestamp, _ in valid_attempts)
    return int(window_seconds - (current_time - oldest_timestamp)) + 1


def record_attempt(bucket_key, window_seconds: int):
    current_time = time.time()
    valid_attempts = _valid_attempts(bucket_key, window_seconds, current_time)
    valid_attempts.append((current_time, 1))
    _rate_limit_store[bucket_key] = valid_attempts
    _cleanup_old_entries()


def hit(bucket_key, max_attempts: int, window_seconds: int):
    """Count one request; return seconds to wait if it is over the limit."""
    wait = retry_after(bucket_key, max_attempts, window_seconds)
    if wait is None:
        record_attempt(bucket_key, window_seconds)
    return wait


def rate_limit(max_attempts: int, window_seconds: int):
    """
    Rate limiting decorator using sliding window.
    
    Args:
        max_attempts: Maximum number of requests allowed
        window_seconds: Time window in seconds
        
    Example:
        @rate_limit(max_attempts=5, window_seconds=60)  # 5 requests per minute
        async def login():
            ...
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            # Skip rate limiting for OPTIONS (CORS preflight)
            if request.method == "OPTIONS":
                return await fn(*args, **kwargs)
            
            client_ip = _get_client_ip()
            bucket_key = (request.endpoint or request.path, client_ip)
            
            # Keep each endpoint independent so login failures do not consume the
            # password-reset allowance (or vice versa) for the same client IP.
            wait = hit(bucket_key, max_attempts, window_seconds)
            if wait is not None:
                return jsonify({
                    "error": "Rate limit exceeded",
                    "message": f"Too many attempts. Please try again in {wait} seconds.",
                    "retry_after": wait
                }), 429

            # Proceed with the request
            return await fn(*args, **kwargs)
        
        return wrapper
    return decorator


def reset_rate_limit(ip_address: str = None):
    """
    Reset rate limit for an IP address (useful for testing or admin override).
    
    Args:
        ip_address: IP to reset, or None to reset all
    """
    if ip_address:
        for key in [key for key in _rate_limit_store if key[1] == ip_address]:
            del _rate_limit_store[key]
    else:
        _rate_limit_store.clear()


def get_rate_limit_status(ip_address: str = None):
    """
    Get current rate limit status for debugging/monitoring.
    
    Args:
        ip_address: IP to check, or None for all IPs
        
    Returns:
        Dict with rate limit statistics
    """
    if ip_address:
        buckets = {
            endpoint: attempts
            for (endpoint, stored_ip), attempts in _rate_limit_store.items()
            if stored_ip == ip_address
        }
        attempts = [attempt for bucket in buckets.values() for attempt in bucket]
        return {
            "ip": ip_address,
            "total_attempts": sum(
                count
                for attempts in buckets.values()
                for _, count in attempts
            ),
            "attempts": attempts,
            "endpoints": buckets,
        }
    else:
        ips = {}
        for (_, ip), attempts in _rate_limit_store.items():
            ips[ip] = ips.get(ip, 0) + sum(count for _, count in attempts)

        return {
            "total_ips": len(ips),
            "ips": ips,
            "total_buckets": len(_rate_limit_store),
            "buckets": {
                f"{endpoint}:{ip}": sum(count for _, count in attempts)
                for (endpoint, ip), attempts in _rate_limit_store.items()
            }
        }
