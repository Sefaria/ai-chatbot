"""
Fetch chatbot config from Sefaria's RemoteConfig API.

The chatbot backend is a separate service and does not have access to Sefaria's
RemoteConfig DB. It fetches the limit from Sefaria's public /api/remote-config endpoint.
"""

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger("chat")

SEFARIA_BASE_URL = os.environ.get("SEFARIA_API_BASE_URL", "https://www.sefaria.org")
MAX_PROMPTS_KEY = "feature.chatbot.max_prompts"
CACHE_TTL_SECONDS = 300  # 5 minutes
# Set CHATBOT_DISABLE_REMOTE_CONFIG_CACHE=1 to bypass cache (for debugging)
CACHE_DISABLED = os.environ.get("CHATBOT_DISABLE_REMOTE_CONFIG_CACHE", "").lower() in ("1", "true", "yes")

_cached_max_prompts: int | None = None
_cache_timestamp: float = 0
_cache_lock = threading.Lock()


def get_max_prompts() -> int:
    """
    Return the max prompts limit from Sefaria RemoteConfig or env fallback.

    - Fetches from Sefaria's /api/remote-config
    - Caches result for 5 minutes
    - Fallback: CHATBOT_MAX_PROMPTS env var, or 100 if unset
    - Returns 0 for unlimited (if config explicitly sets 0)
    """
    global _cached_max_prompts, _cache_timestamp

    env_override = os.environ.get("CHATBOT_MAX_PROMPTS")
    if env_override is not None:
        try:
            result = int(env_override)
            logger.info("[prompt-limit] Using CHATBOT_MAX_PROMPTS env override: %s", result)
            return result
        except ValueError:
            logger.warning("[prompt-limit] Invalid CHATBOT_MAX_PROMPTS env: %s", env_override)

    now = time.time()
    with _cache_lock:
        if (
            not CACHE_DISABLED
            and _cached_max_prompts is not None
            and (now - _cache_timestamp) < CACHE_TTL_SECONDS
        ):
            logger.debug("[prompt-limit] Cache hit: max_prompts=%s", _cached_max_prompts)
            return _cached_max_prompts

    url = f"{SEFARIA_BASE_URL.rstrip('/')}/api/remote-config/"
    logger.info("[prompt-limit] Fetching RemoteConfig from Sefaria: url=%s", url)
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            data = resp.json() if resp.status_code == 200 else None
            logger.info(
                "[prompt-limit] RemoteConfig fetch: status=%s, response_keys=%s",
                resp.status_code,
                list(data.keys()) if data else None,
            )
            resp.raise_for_status()
        value = data.get(MAX_PROMPTS_KEY)
        if value is not None:
            result = int(value)
            with _cache_lock:
                _cached_max_prompts = result
                _cache_timestamp = time.time()
            logger.info("[prompt-limit] Fetched from RemoteConfig: %s=%s", MAX_PROMPTS_KEY, result)
            return result
        logger.warning(
            "[prompt-limit] Key %s not found in RemoteConfig. Available keys: %s",
            MAX_PROMPTS_KEY,
            list(data.keys()),
        )
    except httpx.HTTPStatusError as e:
        logger.warning(
            "[prompt-limit] RemoteConfig HTTP error: %s %s body=%s",
            e.response.status_code,
            e.response.reason_phrase,
            e.response.text[:500] if e.response.text else None,
        )
    except Exception as e:
        logger.warning("[prompt-limit] Failed to fetch RemoteConfig from Sefaria: %s", e, exc_info=True)

    # Fallback - do NOT cache so we retry fetch on next request
    logger.info(
        "[prompt-limit] Using fallback: max_prompts=100 (fetch failed or key missing). "
        "Check: 1) SEFARIA_API_BASE_URL points to your Sefaria instance, "
        "2) RemoteConfigEntry exists with key=%s",
        MAX_PROMPTS_KEY,
    )
    with _cache_lock:
        _cached_max_prompts = 100
        _cache_timestamp = 0  # Don't cache fallback - retry next time
    return 100
