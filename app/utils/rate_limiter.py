"""
Simple in-memory rate limiter for authentication endpoints.

For production with multiple server instances, consider Redis-backed rate limiting.
This implementation uses a sliding window counter approach.
"""
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
    """Use the socket peer; arbitrary forwarded headers are client-controlled.

    A shared proxy bucket is conservative until trusted ingress is configured.
    """
    return request.remote_addr or 'unknown'



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
            current_time = time.time()
            
            # Keep each endpoint independent so login failures do not consume the
            # password-reset allowance (or vice versa) for the same client IP.
            attempts = _rate_limit_store[bucket_key]
            
            # Remove expired attempts (outside the window)
            cutoff_time = current_time - window_seconds
            valid_attempts = [
                (timestamp, count) for timestamp, count in attempts
                if timestamp > cutoff_time
            ]
            
            # Count total attempts in the window
            total_attempts = sum(count for _, count in valid_attempts)
            
            # Check if limit exceeded
            if total_attempts >= max_attempts:
                # Find oldest attempt to calculate retry time
                if valid_attempts:
                    oldest_timestamp = min(timestamp for timestamp, _ in valid_attempts)
                    retry_after = int(window_seconds - (current_time - oldest_timestamp)) + 1
                else:
                    retry_after = window_seconds
                
                return jsonify({
                    "error": "Rate limit exceeded",
                    "message": f"Too many attempts. Please try again in {retry_after} seconds.",
                    "retry_after": retry_after
                }), 429
            
            # Record this attempt
            valid_attempts.append((current_time, 1))
            _rate_limit_store[bucket_key] = valid_attempts
            
            # Periodic cleanup
            _cleanup_old_entries()
            
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
