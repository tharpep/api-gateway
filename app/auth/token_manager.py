"""Single source of truth for the Google OAuth access token.

All Google-backed routers (calendar, tasks, email, storage, sheets) share one
refresh token (see ALL_SCOPES in app.auth.google) and therefore one valid
access token at a time. Previously each router kept its own module-level
cache and its own copy-pasted 401-retry logic, which meant five independent
refreshes racing each other under concurrent load. This module holds the one
cache behind a lock and exposes a single retrying request helper.
"""

import asyncio

import httpx
from fastapi import HTTPException

from app.auth.google import GoogleOAuth, TokenData
from app.config import settings

_oauth = GoogleOAuth()
_cached_token: TokenData | None = None
_refresh_lock = asyncio.Lock()


async def get_access_token() -> str:
    """Return a valid access token, refreshing under a lock if needed."""
    global _cached_token

    if _cached_token is not None and not _oauth.is_token_expired(_cached_token):
        return _cached_token.access_token

    async with _refresh_lock:
        # Another caller may have refreshed while we were waiting for the lock.
        if _cached_token is not None and not _oauth.is_token_expired(_cached_token):
            return _cached_token.access_token

        if not settings.google_refresh_token:
            raise HTTPException(503, "Google refresh token not configured")

        _cached_token = await _oauth.refresh_token(settings.google_refresh_token)
        return _cached_token.access_token


def invalidate_token() -> None:
    """Force the next get_access_token() call to refresh."""
    global _cached_token
    _cached_token = None


async def google_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """Send an authenticated Google API request, retrying once on a 401."""
    token = await get_access_token()
    headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
    response = await client.request(method, url, headers=headers, **kwargs)

    if response.status_code == 401:
        async with _refresh_lock:
            invalidate_token()
        token = await get_access_token()
        headers["Authorization"] = f"Bearer {token}"
        response = await client.request(method, url, headers=headers, **kwargs)

    return response
