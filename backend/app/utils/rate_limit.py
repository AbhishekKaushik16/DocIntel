import time
import asyncio
import logging
from redis.asyncio import Redis
from app.config import settings

logger = logging.getLogger(__name__)

# Global singleton client
_redis_client = None

def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url)
    return _redis_client

async def throttle_gemini_request(rpm: int = 14):
    """
    Enforce a global rate limit across all Celery workers for Gemini API calls.
    Google's Free Tier allows 15 RPM. We use 14 to be safe.
    This strictly spaces out requests by (60 / rpm) seconds.
    """
    # If LLM isn't enabled or provider isn't gemini, no need to throttle
    if not settings.llm_enabled or settings.llm_provider.lower() != "gemini":
        return

    redis = get_redis_client()
    gap = 60.0 / rpm
    
    # Lua script for atomic get-and-check-and-set
    # It reads the last timestamp. If (now - last) < gap, returns the sleep time.
    # Otherwise, updates the timestamp to now and returns 0.
    script = f"""
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local last = redis.call("GET", key)
    
    if last then
        local elapsed = now - tonumber(last)
        if elapsed < {gap} then
            return {gap} - elapsed
        end
    end
    
    redis.call("SET", key, tostring(now))
    redis.call("EXPIRE", key, 10)
    return 0
    """
    
    while True:
        wait_time = await redis.eval(script, 1, "gemini_rate_limit", time.time())
        if wait_time == 0:
            break
            
        wait_time = float(wait_time)
        logger.info(f"Throttling Gemini request for {wait_time:.2f} seconds to respect {rpm} RPM limit...")
        
        # Sleep slightly longer to ensure the next iteration passes the threshold
        await asyncio.sleep(wait_time + 0.1)
    
    logger.info(f"Rate limiter passed. Proceeding with Gemini request (gap=4.28s).")
