"""SearchResult — the pipeline's ground-truth shape for discovery (§7).

Every adapter returns this exact shape. Evidence ledger, dedupe, preflight and
citation formatting all key off these fields.

Rules (from the search-layer spec §7):
- ``snippet`` must be non-empty; adapters synthesize from ``title`` if the
  provider returns none — never an empty string.
- ``url`` must be absolute http(s) with tracking params stripped.
- ``score`` normalized to [0, 1] inside the adapter.
- ``raw`` holds the provider payload for debug — never used downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str           # e.g. "duckduckgo", "you.com", "exa"
    backend: str          # adapter name: "SearXNG" | "You" | "Exa" | "Tavily" | "Yep"
    score: float = 0.0    # 0.0-1.0, provider-normalized
    category: str = "web"  # web | news | science | industry
    published_date: str | None = None
    # OVERHAUL5 W1 (D-03): True when this result can serve a general-web
    # query (web engine / non-academic host). Scholar fan-out rescues are
    # tagged False so the web-class quality trigger never counts them.
    web_class: bool = True
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
            "backend": self.backend,
            "score": self.score,
            "category": self.category,
            "published_date": self.published_date,
            "web_class": self.web_class,
        }


_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                    "utm_content", "fbclid", "gclid", "mc_cid", "mc_eid",
                    "igshid", "yclid", "ref_src", "ref_url"}


def clean_url(url: str) -> str:
    """Strip tracking params and normalize a result URL."""
    try:
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return (url or "").strip()
        kept = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k.lower() not in _TRACKING_PARAMS]
        return urlunparse(parsed._replace(
            query=urlencode(kept, doseq=True) if kept else "",
            fragment="",
        ))
    except Exception:  # noqa: BLE001 - never break the pipeline on a bad URL
        return (url or "").strip()


def dedupe_key(url: str) -> tuple[str, str]:
    """Dedupe identity: netloc + path (query ignored), tracking stripped."""
    cleaned = clean_url(url)
    parsed = urlparse(cleaned)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return (host, parsed.path or "/")


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """Dedupe by (netloc, path), keeping the highest-scored version."""
    seen: dict[tuple[str, str], SearchResult] = {}
    for r in results:
        key = dedupe_key(r.url)
        current = seen.get(key)
        if current is None or r.score > current.score:
            seen[key] = r
    return list(seen.values())


def ensure_snippet(result: SearchResult) -> SearchResult:
    """Synthesize a snippet from the title when the provider gave none."""
    if result.snippet and len(result.snippet.strip()) >= 40:
        return result
    snippet = (result.snippet or "").strip() or result.title.strip()
    return SearchResult(
        title=result.title,
        url=result.url,
        snippet=snippet,
        engine=result.engine,
        backend=result.backend,
        score=result.score,
        category=result.category,
        published_date=result.published_date,
        raw=result.raw,
    )
