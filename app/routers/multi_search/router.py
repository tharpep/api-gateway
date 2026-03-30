"""Multi-platform search router.

Individual endpoints: POST /multi-search/{platform}
Aggregate endpoint:   POST /multi-search/aggregate
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.config import settings

from .adapters import (
    CUSTOM_RSS_FEEDS,
    fetch_fact_checks,
    search_bluesky,
    search_custom_rss,
    search_gnews,
    search_google_news_rss,
    search_hn,
    search_reddit,
)
from .models import (
    AggregateSearchRequest,
    AggregateSearchResponse,
    CorroborationCluster,
    PlatformSearchRequest,
    PlatformSearchResponse,
    RedditSearchRequest,
    UnifiedResult,
)
from .signals import compute_corroboration

logger = logging.getLogger(__name__)

router = APIRouter()

_DIVERSITY_CAP = 0.40  # no single platform exceeds this fraction


# ---------------------------------------------------------------------------
# Individual platform endpoints
# ---------------------------------------------------------------------------


@router.post("/hn")
async def search_hn_endpoint(body: PlatformSearchRequest) -> PlatformSearchResponse:
    results = await search_hn(
        query=body.query,
        max_results=body.max_results,
        sort=body.sort,
        since=body.since,
    )
    return PlatformSearchResponse(
        query=body.query,
        platform="hn",
        total_results=len(results),
        results=results,
    )


@router.post("/google-news-rss")
async def search_google_news_rss_endpoint(body: PlatformSearchRequest) -> PlatformSearchResponse:
    results = await search_google_news_rss(
        query=body.query,
        max_results=body.max_results,
        since=body.since,
    )
    return PlatformSearchResponse(
        query=body.query,
        platform="google_news_rss",
        total_results=len(results),
        results=results,
    )


@router.post("/bluesky")
async def search_bluesky_endpoint(body: PlatformSearchRequest) -> PlatformSearchResponse:
    results = await search_bluesky(
        query=body.query,
        max_results=body.max_results,
        sort=body.sort,
        since=body.since,
    )
    return PlatformSearchResponse(
        query=body.query,
        platform="bluesky",
        total_results=len(results),
        results=results,
    )


@router.post("/gnews")
async def search_gnews_endpoint(body: PlatformSearchRequest) -> PlatformSearchResponse:
    if not settings.gnews_api_key:
        raise HTTPException(503, "GNews API key not configured")
    results = await search_gnews(
        query=body.query,
        max_results=body.max_results,
        sort=body.sort,
        since=body.since,
    )
    return PlatformSearchResponse(
        query=body.query,
        platform="gnews",
        total_results=len(results),
        results=results,
    )


@router.post("/reddit")
async def search_reddit_endpoint(body: RedditSearchRequest) -> PlatformSearchResponse:
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise HTTPException(503, "Reddit credentials not configured")
    results = await search_reddit(
        query=body.query,
        max_results=body.max_results,
        sort=body.sort,
        since=body.since,
        subreddit=body.subreddit,
    )
    return PlatformSearchResponse(
        query=body.query,
        platform="reddit",
        total_results=len(results),
        results=results,
    )


@router.post("/rss")
async def search_rss_endpoint(body: PlatformSearchRequest) -> PlatformSearchResponse:
    if not CUSTOM_RSS_FEEDS:
        raise HTTPException(503, "No custom RSS feeds configured")
    results = await search_custom_rss(
        query=body.query,
        max_results=body.max_results,
        since=body.since,
    )
    return PlatformSearchResponse(
        query=body.query,
        platform="rss",
        total_results=len(results),
        results=results,
    )


# ---------------------------------------------------------------------------
# Aggregate endpoint
# ---------------------------------------------------------------------------


def _enforce_diversity(results: list[UnifiedResult], cap: float) -> list[UnifiedResult]:
    """Trim results so no single platform exceeds cap% of total."""
    total = len(results)
    if total == 0:
        return results
    max_per = max(int(total * cap), 1)
    counts: dict[str, int] = {}
    kept: list[UnifiedResult] = []
    for r in results:
        counts.setdefault(r.source_platform, 0)
        if counts[r.source_platform] < max_per:
            kept.append(r)
            counts[r.source_platform] += 1
    return kept


def _bucket_results(results: list[UnifiedResult]) -> dict[str, list[int]]:
    now = datetime.now(UTC)
    buckets: dict[str, list[int]] = {"24h": [], "week": [], "month": []}
    for i, r in enumerate(results):
        try:
            ts = datetime.fromisoformat(r.timestamp.replace("Z", "+00:00"))
            delta = now - ts
            if delta.days < 1:
                buckets["24h"].append(i)
            if delta.days < 7:
                buckets["week"].append(i)
            if delta.days < 30:
                buckets["month"].append(i)
        except Exception:
            pass
    return buckets


@router.post("/aggregate")
async def aggregate_search(body: AggregateSearchRequest) -> AggregateSearchResponse:
    # 1. Resolve platforms
    requested = (
        set(body.platforms)
        if body.platforms
        else {"hn", "google_news_rss", "bluesky", "gnews", "reddit", "rss"}
    )

    # GNews unavailable → auto-add google_news_rss as fallback
    if "gnews" in requested and not settings.gnews_api_key:
        requested.add("google_news_rss")

    # Skip platforms that have no credentials configured
    active: list[str] = []
    skipped: list[str] = []
    for p in requested:
        if p == "reddit" and (not settings.reddit_client_id or not settings.reddit_client_secret):
            skipped.append(p)
        elif p == "gnews" and not settings.gnews_api_key:
            skipped.append(p)
        elif p == "rss" and not CUSTOM_RSS_FEEDS:
            skipped.append(p)
        else:
            active.append(p)

    if skipped:
        logger.debug("Skipping unconfigured platforms: %s", skipped)

    # 2. Build parallel tasks (adapters + fact check)
    adapter_map = {
        "hn": lambda: search_hn(body.query, body.max_results, body.sort, body.since),
        "google_news_rss": lambda: search_google_news_rss(body.query, body.max_results, body.since),
        "bluesky": lambda: search_bluesky(body.query, body.max_results, body.sort, body.since),
        "gnews": lambda: search_gnews(body.query, body.max_results, body.sort, body.since),
        "reddit": lambda: search_reddit(body.query, body.max_results, body.sort, body.since),
        "rss": lambda: search_custom_rss(body.query, body.max_results, body.since),
    }

    tasks = [adapter_map[p]() for p in active]
    fact_task = fetch_fact_checks(body.query)

    all_task_results = await asyncio.gather(*tasks, fact_task, return_exceptions=True)
    platform_results = all_task_results[:-1]
    fact_check_result = all_task_results[-1]

    fact_checks: list[dict] = fact_check_result if isinstance(fact_check_result, list) else []

    # 3. Collect + merge
    all_results: list[UnifiedResult] = []
    sources_queried = list(active)

    for platform, result_or_exc in zip(active, platform_results):
        if isinstance(result_or_exc, Exception):
            logger.warning("Platform %s error during aggregate: %s", platform, result_or_exc)
            continue
        all_results.extend(result_or_exc)

    # 4. Apply fact check hits to all results (query-level signal)
    if fact_checks:
        for r in all_results:
            r.fact_check_hits = fact_checks

    # 5. Corroboration clustering
    clusters: list[CorroborationCluster] = []
    if all_results:
        clusters, all_results = compute_corroboration(all_results)

    # 6. Sort (pre-diversity to preserve best results per platform)
    if body.sort == "date":
        all_results.sort(key=lambda r: r.timestamp, reverse=True)
    else:
        # Relevance proxy: engagement_velocity > score > timestamp
        def _sort_key(r: UnifiedResult) -> tuple:
            vel = r.engagement_velocity or 0.0
            score = r.score or 0
            try:
                ts = datetime.fromisoformat(r.timestamp.replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = 0.0
            return (vel, score, ts)

        all_results.sort(key=_sort_key, reverse=True)

    # 7. Diversity cap
    all_results = _enforce_diversity(all_results, _DIVERSITY_CAP)

    # 8. Limit
    all_results = all_results[: body.max_results]

    # 9. Final source distribution + temporal buckets
    source_distribution: dict[str, int] = {}
    for r in all_results:
        source_distribution[r.source_platform] = source_distribution.get(r.source_platform, 0) + 1

    return AggregateSearchResponse(
        query=body.query,
        sources_queried=sources_queried,
        total_results=len(all_results),
        results=all_results,
        temporal_buckets=_bucket_results(all_results),
        source_distribution=source_distribution,
        corroboration_clusters=clusters,
    )
