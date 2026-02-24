"""Search endpoint — Tavily web search and URL extraction."""

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

router = APIRouter()

TAVILY_API = "https://api.tavily.com"


def _require_key() -> str:
    if not settings.tavily_api_key:
        raise HTTPException(503, "Tavily API key not configured")
    return settings.tavily_api_key



class WebSearchRequest(BaseModel):
    query: str
    max_results: int = 5
    search_depth: str = "basic"  # "basic" or "advanced"


class FetchUrlRequest(BaseModel):
    url: str



@router.post("/web")
async def web_search(body: WebSearchRequest):
    """Search the web via Tavily. Returns titles, URLs, and pre-extracted content snippets."""
    api_key = _require_key()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TAVILY_API}/search",
            json={
                "api_key": api_key,
                "query": body.query,
                "max_results": body.max_results,
                "search_depth": body.search_depth,
            },
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Tavily API error: {response.text}")

    return response.json()


@router.post("/web/fetch")
async def fetch_url(body: FetchUrlRequest):
    """Fetch and extract the readable text content from a specific URL via Tavily."""
    api_key = _require_key()

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TAVILY_API}/extract",
            json={
                "api_key": api_key,
                "urls": [body.url],
            },
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Tavily API error: {response.text}")

    return response.json()
