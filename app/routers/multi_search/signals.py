"""Deterministic bias and validity signals for search results.

All functions are pure (no I/O). Called during result normalization.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher

from .models import CorroborationCluster, UnifiedResult

# ---------------------------------------------------------------------------
# Credibility tier
# ---------------------------------------------------------------------------

_WIRE_DOMAINS: frozenset[str] = frozenset(
    {
        "apnews.com",
        "reuters.com",
        "afp.com",
        "upi.com",
    }
)

_ESTABLISHED_DOMAINS: frozenset[str] = frozenset(
    {
        "bbc.com",
        "bbc.co.uk",
        "nytimes.com",
        "washingtonpost.com",
        "theguardian.com",
        "wsj.com",
        "economist.com",
        "ft.com",
        "npr.org",
        "pbs.org",
        "cnn.com",
        "abcnews.go.com",
        "nbcnews.com",
        "cbsnews.com",
        "usatoday.com",
        "latimes.com",
        "politico.com",
        "thehill.com",
        "bloomberg.com",
        "cnbc.com",
        "nature.com",
        "science.org",
        "arstechnica.com",
        "wired.com",
        "theatlantic.com",
        "axios.com",
        "vox.com",
        "propublica.org",
        "theintercept.com",
        "slate.com",
        "time.com",
        "newsweek.com",
        "foreignpolicy.com",
    }
)

_INDEPENDENT_DOMAIN_PARTS: tuple[str, ...] = (
    "substack.com",
    "medium.com",
    "ghost.io",
    "wordpress.com",
    "beehiiv.com",
    "buttondown.email",
)

_SOCIAL_PLATFORMS: frozenset[str] = frozenset({"reddit", "hn", "bluesky"})


def get_credibility_tier(source_domain: str, source_platform: str) -> str | None:
    if source_platform in _SOCIAL_PLATFORMS:
        return "social"
    domain = source_domain.lower()
    if domain in _WIRE_DOMAINS:
        return "wire"
    if domain in _ESTABLISHED_DOMAINS:
        return "established"
    if any(part in domain for part in _INDEPENDENT_DOMAIN_PARTS):
        return "independent"
    return None


# ---------------------------------------------------------------------------
# Content type
# ---------------------------------------------------------------------------

_CONTENT_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"/opinion[s]?/|/op-ed/|/commentary/", re.I), "opinion"),
    (re.compile(r"/editorial[s]?/", re.I), "opinion"),
    (re.compile(r"/analysis/|/explainer[s]?/", re.I), "analysis"),
    (re.compile(r"/blog[s]?/|/post[s]?/", re.I), "blog"),
    (re.compile(r"/press-release[s]?/|/pr/|/news-release[s]?/", re.I), "press_release"),
]


def classify_content_type(url: str, source_platform: str) -> str:
    if source_platform in _SOCIAL_PLATFORMS:
        return "social"
    for pattern, ctype in _CONTENT_TYPE_PATTERNS:
        if pattern.search(url):
            return ctype
    return "reporting"


# ---------------------------------------------------------------------------
# Named source attribution density
# ---------------------------------------------------------------------------

_ATTRIBUTION_RE = re.compile(
    r"(?:"
    r"according to [A-Z][a-z]+"
    r"|[A-Z][a-z]+ [A-Z][a-z]+ "
    r"(?:said|stated|told|confirmed|denied|announced|reported|explained|noted)"
    r"|(?:said|stated|told|confirmed|announced) [A-Z][a-z]+ [A-Z][a-z]+"
    r"|sources? (?:say|said|tell|told|report|confirm|indicate)"
    r"|a (?:senior|former|current) (?:official|executive|spokesperson|analyst|researcher)"
    r")",
    re.MULTILINE,
)


def count_named_sources(text: str) -> int:
    if not text:
        return 0
    return len(_ATTRIBUTION_RE.findall(text))


# ---------------------------------------------------------------------------
# Hedge ratio
# ---------------------------------------------------------------------------

_HEDGE_WORDS: frozenset[str] = frozenset(
    {
        "allegedly",
        "apparently",
        "claimed",
        "claims",
        "could",
        "likely",
        "may",
        "might",
        "perhaps",
        "possibly",
        "potentially",
        "purportedly",
        "reportedly",
        "rumored",
        "seemingly",
        "supposedly",
        "unclear",
        "unconfirmed",
        "unverified",
        "appears",
        "suggests",
        "suggested",
        "believed",
        "alleged",
        "apparent",
        "possible",
        "probable",
    }
)


def compute_hedge_ratio(text: str) -> float:
    if not text:
        return 0.0
    words = text.lower().split()
    if not words:
        return 0.0
    hedge_count = sum(1 for w in words if w.strip(".,;:!?\"'()") in _HEDGE_WORDS)
    return round(hedge_count / len(words), 4)


# ---------------------------------------------------------------------------
# Engagement velocity
# ---------------------------------------------------------------------------


def compute_engagement_velocity(
    score: int | None,
    timestamp: str,
    source_platform: str,
) -> float | None:
    if score is None or source_platform not in _SOCIAL_PLATFORMS:
        return None
    try:
        posted = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        hours = (datetime.now(UTC) - posted).total_seconds() / 3600
        if hours < 0.1:  # too fresh — avoid instability
            return None
        return round(score / hours, 2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Composite signal application
# ---------------------------------------------------------------------------


def compute_signals(result: UnifiedResult) -> UnifiedResult:
    """Apply all deterministic signals to a UnifiedResult in-place."""
    result.credibility_tier = get_credibility_tier(result.source_domain, result.source_platform)
    result.content_type = classify_content_type(result.url, result.source_platform)
    result.named_source_count = count_named_sources(result.content_snippet)
    result.hedge_ratio = compute_hedge_ratio(result.content_snippet)
    result.engagement_velocity = compute_engagement_velocity(
        result.score, result.timestamp, result.source_platform
    )
    return result


# ---------------------------------------------------------------------------
# Corroboration clustering
# ---------------------------------------------------------------------------

_CORROBORATION_THRESHOLD = 0.55


def compute_corroboration(
    results: list[UnifiedResult],
) -> tuple[list[CorroborationCluster], list[UnifiedResult]]:
    """Group results about the same story by title similarity.

    Skips pairs from the same source domain (same outlet ≠ corroboration).
    Sets corroboration_count on each result and returns cluster models.
    """
    n = len(results)
    clusters: list[list[int]] = []
    assigned: set[int] = set()

    for i in range(n):
        if i in assigned:
            continue
        cluster = [i]
        assigned.add(i)
        title_i = results[i].title.lower()
        for j in range(i + 1, n):
            if j in assigned:
                continue
            # Same domain = same publisher, skip
            if results[i].source_domain == results[j].source_domain:
                continue
            ratio = SequenceMatcher(None, title_i, results[j].title.lower()).ratio()
            if ratio >= _CORROBORATION_THRESHOLD:
                cluster.append(j)
                assigned.add(j)
        if len(cluster) > 1:
            clusters.append(cluster)

    # Set corroboration_count on each clustered result
    for cluster in clusters:
        for idx in cluster:
            results[idx].corroboration_count = len(cluster) - 1

    cluster_models = [
        CorroborationCluster(
            story=results[cluster[0]].title,
            sources=[results[i].source_platform for i in cluster],
            urls=[results[i].url for i in cluster],
            count=len(cluster),
        )
        for cluster in clusters
    ]

    return cluster_models, results
