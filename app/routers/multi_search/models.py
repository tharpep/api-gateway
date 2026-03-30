"""Pydantic models for multi-platform search."""

from pydantic import BaseModel


class PlatformSearchRequest(BaseModel):
    query: str
    max_results: int = 10
    sort: str = "relevance"  # "relevance" | "date"
    since: str | None = None  # ISO 8601 — only return results newer than this


class RedditSearchRequest(PlatformSearchRequest):
    subreddit: str | None = None  # optional subreddit scope


class AggregateSearchRequest(BaseModel):
    query: str
    max_results: int = 25
    platforms: list[str] | None = None  # None = all available
    since: str | None = None
    sort: str = "relevance"


class UnifiedResult(BaseModel):
    title: str
    url: str
    content_snippet: str
    source_platform: str  # "reddit"|"hn"|"bluesky"|"gnews"|"google_news_rss"|"rss"
    source_domain: str
    author: str | None = None
    timestamp: str  # ISO 8601
    score: int | None = None
    comment_count: int | None = None
    media_urls: list[str] = []
    # Bias/validity signals (computed during normalization)
    credibility_tier: str | None = None  # "wire"|"established"|"independent"|"social"
    # "reporting"|"opinion"|"analysis"|"blog"|"press_release"|"social"
    content_type: str = "reporting"
    named_source_count: int = 0
    hedge_ratio: float = 0.0
    fact_check_hits: list[dict] = []
    corroboration_count: int = 0
    engagement_velocity: float | None = None


class CorroborationCluster(BaseModel):
    story: str  # representative title
    sources: list[str]  # source_platform values
    urls: list[str]
    count: int


class AggregateSearchResponse(BaseModel):
    query: str
    sources_queried: list[str]
    total_results: int
    results: list[UnifiedResult]
    temporal_buckets: dict[str, list[int]]  # "24h"|"week"|"month" → indices into results
    source_distribution: dict[str, int]
    corroboration_clusters: list[CorroborationCluster]


class PlatformSearchResponse(BaseModel):
    query: str
    platform: str
    total_results: int
    results: list[UnifiedResult]
