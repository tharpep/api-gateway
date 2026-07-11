"""Shared pooled httpx.AsyncClient, owned by the FastAPI lifespan.

Every router previously opened a new httpx.AsyncClient per request (per call,
in some cases several times per request). That means a fresh TCP/TLS
handshake per outbound call with no connection reuse. One pooled client per
process, opened at startup and closed at shutdown, lets httpx reuse
connections across requests.
"""

import httpx

_client: httpx.AsyncClient | None = None


async def startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=30.0)


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> httpx.AsyncClient:
    """Return the shared client. Only valid while the app lifespan is active."""
    if _client is None:
        raise RuntimeError("HTTP client not initialized — app lifespan startup() hasn't run")
    return _client
