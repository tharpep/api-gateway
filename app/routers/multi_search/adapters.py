"""Per-platform async search adapters.

Each adapter returns list[UnifiedResult] and degrades gracefully:
- Missing credentials → return [] silently
- HTTP/timeout errors → log warning, return []

Timeouts: 15s per adapter (10s per feed for custom RSS).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

import httpx

from app.config import settings

from .models import UnifiedResult
from .signals import compute_signals

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return ""


def _trim(text: str | None, max_len: int = 500) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def _to_iso(value: str | float | int | None) -> str:
    """Convert various timestamp formats to ISO 8601."""
    if value is None:
        return datetime.now(UTC).isoformat()
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    v = str(value).strip()
    # Try RFC 2822 (RSS pubDate)
    try:
        return parsedate_to_datetime(v).isoformat()
    except Exception:
        pass
    # Try ISO 8601 variants
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
    except Exception:
        pass
    return datetime.now(UTC).isoformat()


def _since_ts(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        return datetime.fromisoformat(since.replace("Z", "+00:00"))
    except Exception:
        return None


def _after_since(timestamp: str, since_dt: datetime | None) -> bool:
    if since_dt is None:
        return True
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return ts > since_dt
    except Exception:
        return True


# ---------------------------------------------------------------------------
# HN (Algolia — no auth)
# ---------------------------------------------------------------------------


async def search_hn(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    since: str | None = None,
) -> list[UnifiedResult]:
    endpoint = "search_by_date" if sort == "date" else "search"
    params: dict = {
        "query": query,
        "tags": "story",
        "hitsPerPage": max_results,
    }
    since_dt = _since_ts(since)
    if since_dt:
        params["numericFilters"] = f"created_at_i>{int(since_dt.timestamp())}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://hn.algolia.com/api/v1/{endpoint}", params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("HN adapter error: %s", exc)
        return []

    results = []
    for hit in data.get("hits", []):
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        ts = _to_iso(hit.get("created_at"))
        r = UnifiedResult(
            title=hit.get("title") or "(untitled)",
            url=url,
            content_snippet=_trim(hit.get("story_text")),
            source_platform="hn",
            source_domain=_domain(url),
            author=hit.get("author"),
            timestamp=ts,
            score=hit.get("points"),
            comment_count=hit.get("num_comments"),
        )
        results.append(compute_signals(r))

    return results


# ---------------------------------------------------------------------------
# Google News RSS (no auth — fallback for GNews)
# ---------------------------------------------------------------------------


async def search_google_news_rss(
    query: str,
    max_results: int = 10,
    since: str | None = None,
) -> list[UnifiedResult]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    since_dt = _since_ts(since)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except Exception as exc:
        logger.warning("Google News RSS adapter error: %s", exc)
        return []

    results = []
    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item")[:max_results]:
        try:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = item.findtext("pubDate")
            ts = _to_iso(pub_date)

            if not _after_since(ts, since_dt):
                continue

            # Source domain from <source url="..."> attribute
            source_el = item.find("source")
            source_url = source_el.get("url", "") if source_el is not None else ""
            source_domain = _domain(source_url) or _domain(link)

            desc = item.findtext("description") or ""
            r = UnifiedResult(
                title=title or "(untitled)",
                url=link,
                content_snippet=_trim(desc),
                source_platform="google_news_rss",
                source_domain=source_domain,
                author=None,
                timestamp=ts,
            )
            results.append(compute_signals(r))
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Bluesky (public API — no auth required for search)
# ---------------------------------------------------------------------------


async def search_bluesky(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    since: str | None = None,
) -> list[UnifiedResult]:
    params: dict = {"q": query, "limit": min(max_results, 25)}
    if sort == "date":
        params["sort"] = "latest"
    since_dt = _since_ts(since)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                params=params,
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Bluesky adapter error: %s", exc)
        return []

    results = []
    for post in data.get("posts", []):
        try:
            record = post.get("record", {})
            text = record.get("text", "")
            created_at = record.get("createdAt", "")
            ts = _to_iso(created_at)

            if not _after_since(ts, since_dt):
                continue

            author = post.get("author", {})
            handle = author.get("handle", "")
            display = author.get("displayName") or handle

            # Construct post URL from AT URI: at://did:plc:.../app.bsky.feed.post/rkey
            uri = post.get("uri", "")
            rkey = uri.split("/")[-1] if uri else ""
            post_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""

            # Extract media URLs from embed
            media_urls: list[str] = []
            embed = post.get("embed", {})
            for img in embed.get("images", []):
                thumb = img.get("thumb") or img.get("fullsize")
                if thumb:
                    media_urls.append(thumb)

            title = text[:120].replace("\n", " ") if text else "(no text)"
            r = UnifiedResult(
                title=title,
                url=post_url,
                content_snippet=_trim(text),
                source_platform="bluesky",
                source_domain="bsky.app",
                author=display,
                timestamp=ts,
                score=post.get("likeCount"),
                comment_count=post.get("replyCount"),
                media_urls=media_urls,
            )
            results.append(compute_signals(r))
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# GNews (requires gnews_api_key)
# ---------------------------------------------------------------------------


async def search_gnews(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    since: str | None = None,
) -> list[UnifiedResult]:
    if not settings.gnews_api_key:
        return []

    params: dict = {
        "q": query,
        "token": settings.gnews_api_key,
        "max": min(max_results, 10),
        "lang": "en",
    }
    if sort == "date":
        params["sortby"] = "publishedAt"
    if since:
        # GNews expects YYYY-MM-DDThh:mm:ssZ
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            params["from"] = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://gnews.io/api/v4/search", params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("GNews adapter error: %s", exc)
        return []

    results = []
    for article in data.get("articles", []):
        try:
            url = article.get("url", "")
            source = article.get("source", {})
            source_domain = _domain(source.get("url", "") or url)

            media_urls = []
            if article.get("image"):
                media_urls.append(article["image"])

            r = UnifiedResult(
                title=article.get("title") or "(untitled)",
                url=url,
                content_snippet=_trim(article.get("content") or article.get("description")),
                source_platform="gnews",
                source_domain=source_domain,
                author=None,
                timestamp=_to_iso(article.get("publishedAt")),
                media_urls=media_urls,
            )
            results.append(compute_signals(r))
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Reddit (OAuth2 app-only)
# ---------------------------------------------------------------------------

_reddit_token: str | None = None
_reddit_token_expires: float = 0.0
_reddit_lock = asyncio.Lock()


async def _get_reddit_token(client: httpx.AsyncClient) -> str:
    global _reddit_token, _reddit_token_expires
    async with _reddit_lock:
        if _reddit_token and time.monotonic() < _reddit_token_expires:
            return _reddit_token
        resp = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            headers={"User-Agent": "sazed-search/1.0"},
        )
        resp.raise_for_status()
        body = resp.json()
        _reddit_token = body["access_token"]
        _reddit_token_expires = time.monotonic() + body.get("expires_in", 3600) - 60
        return _reddit_token


async def search_reddit(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    since: str | None = None,
    subreddit: str | None = None,
) -> list[UnifiedResult]:
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        return []

    since_dt = _since_ts(since)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_reddit_token(client)
            base = "https://oauth.reddit.com"
            path = f"/r/{subreddit}/search.json" if subreddit else "/search.json"
            params = {
                "q": query,
                "sort": sort if sort in ("relevance", "new", "hot", "top") else "relevance",
                "t": "month",
                "limit": max_results,
                "raw_json": 1,
            }
            if subreddit:
                params["restrict_sr"] = 1
            resp = await client.get(
                f"{base}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "sazed-search/1.0",
                },
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Reddit adapter error: %s", exc)
        return []

    results = []
    for child in data.get("data", {}).get("children", []):
        try:
            post = child.get("data", {})
            ts = _to_iso(post.get("created_utc"))

            if not _after_since(ts, since_dt):
                continue

            permalink = post.get("permalink", "")
            is_self = post.get("is_self", False)
            url = (
                f"https://reddit.com{permalink}"
                if is_self
                else (post.get("url") or f"https://reddit.com{permalink}")
            )

            media_urls: list[str] = []
            thumbnail = post.get("thumbnail")
            if thumbnail and thumbnail.startswith("http"):
                media_urls.append(thumbnail)
            preview = post.get("preview", {})
            for img in preview.get("images", [])[:1]:
                src = img.get("source", {}).get("url")
                if src:
                    media_urls.append(src.replace("&amp;", "&"))

            r = UnifiedResult(
                title=post.get("title") or "(untitled)",
                url=url,
                content_snippet=_trim(post.get("selftext")),
                source_platform="reddit",
                source_domain=_domain(url) if not is_self else "reddit.com",
                author=post.get("author"),
                timestamp=ts,
                score=post.get("score"),
                comment_count=post.get("num_comments"),
                media_urls=media_urls,
            )
            results.append(compute_signals(r))
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Custom RSS (keyword-filtered, parallel fetch)
# ---------------------------------------------------------------------------

_ATOM_NS = "http://www.w3.org/2005/Atom"
_DC_NS = "http://purl.org/dc/elements/1.1/"

# ---------------------------------------------------------------------------
# Custom RSS feed list — add/remove feeds here directly
# ---------------------------------------------------------------------------

CUSTOM_RSS_FEEDS: list[str] = [
    "https://blog.google/technology/ai/rss/",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://www.theverge.com/rss/index.xml",
    "https://www.schneier.com/blog/atom.xml",
    "https://krebsonsecurity.com/feed/",
    "https://openai.com/blog/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://simonwillison.net/atom/everything/",
]


def _parse_rss_feed(content: bytes, feed_url: str, query_terms: list[str]) -> list[UnifiedResult]:
    results = []
    try:
        root = ElementTree.fromstring(content)
    except Exception:
        return []

    # Detect Atom vs RSS
    is_atom = root.tag == f"{{{_ATOM_NS}}}feed" or root.tag == "feed"

    if is_atom:
        entries = root.findall(f"{{{_ATOM_NS}}}entry") or root.findall("entry")
        for entry in entries:
            try:

                def at(tag: str) -> str | None:
                    el = entry.find(f"{{{_ATOM_NS}}}{tag}") or entry.find(tag)
                    return el.text if el is not None else None

                title = (at("title") or "").strip()
                summary = at("summary") or at("content") or ""
                combined = f"{title} {summary}".lower()
                if not any(term in combined for term in query_terms):
                    continue

                link_el = entry.find(f"{{{_ATOM_NS}}}link") or entry.find("link")
                link = link_el.get("href", "") if link_el is not None else ""
                updated = at("updated") or at("published") or ""
                author_el = entry.find(f"{{{_ATOM_NS}}}author") or entry.find("author")
                author = None
                if author_el is not None:
                    name_el = author_el.find(f"{{{_ATOM_NS}}}name") or author_el.find("name")
                    author = name_el.text if name_el is not None else None

                r = UnifiedResult(
                    title=title or "(untitled)",
                    url=link,
                    content_snippet=_trim(summary),
                    source_platform="rss",
                    source_domain=_domain(feed_url),
                    author=author,
                    timestamp=_to_iso(updated),
                )
                results.append(compute_signals(r))
            except Exception:
                continue
    else:
        channel = root.find("channel")
        if channel is None:
            return []
        for item in channel.findall("item"):
            try:
                title = (item.findtext("title") or "").strip()
                desc = item.findtext("description") or ""
                combined = f"{title} {desc}".lower()
                if not any(term in combined for term in query_terms):
                    continue

                link = (item.findtext("link") or "").strip()
                pub_date = item.findtext("pubDate")
                author = item.findtext(f"{{{_DC_NS}}}creator") or item.findtext("author")

                media_urls: list[str] = []
                enclosure = item.find("enclosure")
                if enclosure is not None:
                    enc_url = enclosure.get("url")
                    if enc_url:
                        media_urls.append(enc_url)

                r = UnifiedResult(
                    title=title or "(untitled)",
                    url=link,
                    content_snippet=_trim(desc),
                    source_platform="rss",
                    source_domain=_domain(feed_url),
                    author=author,
                    timestamp=_to_iso(pub_date),
                    media_urls=media_urls,
                )
                results.append(compute_signals(r))
            except Exception:
                continue

    return results


async def _fetch_one_rss(
    client: httpx.AsyncClient, feed_url: str, query_terms: list[str]
) -> list[UnifiedResult]:
    try:
        resp = await client.get(feed_url, follow_redirects=True)
        resp.raise_for_status()
        return _parse_rss_feed(resp.content, feed_url, query_terms)
    except Exception as exc:
        logger.warning("Custom RSS feed %s error: %s", feed_url, exc)
        return []


async def search_custom_rss(
    query: str,
    max_results: int = 10,
    since: str | None = None,
) -> list[UnifiedResult]:
    feed_urls = CUSTOM_RSS_FEEDS
    if not feed_urls:
        return []

    query_terms = [t.lower() for t in query.split() if len(t) > 2]
    since_dt = _since_ts(since)

    async with httpx.AsyncClient(timeout=10.0) as client:
        per_feed = await asyncio.gather(
            *[_fetch_one_rss(client, url, query_terms) for url in feed_urls],
            return_exceptions=True,
        )

    all_results: list[UnifiedResult] = []
    for batch in per_feed:
        if isinstance(batch, list):
            all_results.extend(batch)

    # Apply since filter
    if since_dt:
        all_results = [r for r in all_results if _after_since(r.timestamp, since_dt)]

    return all_results[:max_results]


# ---------------------------------------------------------------------------
# Google Fact Check Tools API (no key required)
# ---------------------------------------------------------------------------


async def fetch_fact_checks(query: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                params={"query": query, "languageCode": "en"},
            )
        if not resp.is_success:
            return []
        data = resp.json()
    except Exception as exc:
        logger.warning("Fact Check API error: %s", exc)
        return []

    hits = []
    for claim in data.get("claims", []):
        reviews = [
            {
                "publisher": r.get("publisher", {}).get("name"),
                "url": r.get("url"),
                "rating": r.get("textualRating"),
                "title": r.get("title"),
            }
            for r in claim.get("claimReview", [])
        ]
        hits.append(
            {
                "claim_text": claim.get("text"),
                "claimant": claim.get("claimant"),
                "reviews": reviews,
            }
        )

    return hits
