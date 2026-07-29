"""
HYPERION Unsplash Client — image search, download, and caching.

Unsplash is the ONLY image source in HYPERION (NOT Imagen, NOT stock
photos). It provides:
- Cover images (full-bleed, high resolution)
- Section header images (40% width, contextual)
- Curated fallback library (50-100 generic business images)

This is NOT a generic "search for images" wrapper. It:
- Uses the Unsplash API with proper rate limit handling
- Free tier: 50 req/hr (demo) → 1000 req/hr (production)
- Image file requests (images.unsplash.com) do NOT count against rate limit
- Caches all API search responses to minimize API calls
- Pre-downloads curated library for fallback when API limit is hit
- Max 1 API call per report section (use cached results for similar queries)
- Trigger download endpoint for attribution compliance
- Returns structured results with photographer attribution

Architecture reference: §5.1 — "Free tier: 50 req/hr (demo) → 1000 req/hr
(production). Cover images, section headers, contextual photos. Image file
requests don't count against rate limit."

§5.4 — "Cache all API search responses to minimize API calls.
Pre-download curated library of 50-100 generic business images (boardrooms,
cityscapes, technology, nature, abstract) for fallback. Trigger download
endpoint for attribution compliance. Max 1 API call per report section."

Image selection strategy (§5.4):
"The Presentation Designer specifies exact search terms per section. Not
'business' — but 'modern boardroom meeting' for the market entry section,
'financial charts on screen' for the financial analysis section, 'city
skyline india' for the geographic analysis section. Specific, relevant,
not generic."

Used by: Presentation Designer, Data Visualizer (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

import httpx


@dataclass
class UnsplashImage:
    """A single Unsplash image result."""

    id: str
    url: str = ""                    # Unsplash page URL
    image_url: str = ""              # Direct image URL (images.unsplash.com)
    thumb_url: str = ""              # Thumbnail URL
    width: int = 0
    height: int = 0
    description: str = ""
    alt_description: str = ""
    photographer: str = ""
    photographer_url: str = ""
    unsplash_username: str = ""
    download_url: str = ""           # Trigger download endpoint for attribution
    local_path: str = ""             # Local path after download
    search_term: str = ""            # What search term found this image

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "image_url": self.image_url,
            "thumb_url": self.thumb_url,
            "width": self.width,
            "height": self.height,
            "description": self.description,
            "alt_description": self.alt_description,
            "photographer": self.photographer,
            "photographer_url": self.photographer_url,
            "unsplash_username": self.unsplash_username,
            "download_url": self.download_url,
            "local_path": self.local_path,
            "search_term": self.search_term,
        }


@dataclass
class UnsplashSearchResult:
    """Result of an Unsplash image search."""

    query: str
    images: list[UnsplashImage] = field(default_factory=list)
    total: int = 0
    took_ms: int = 0
    cached: bool = False
    rate_limited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "images": [img.to_dict() for img in self.images],
            "total": self.total,
            "took_ms": self.took_ms,
            "cached": self.cached,
            "rate_limited": self.rate_limited,
        }


class UnsplashClient:
    """Unsplash image search, download, and caching client.

    Provides image search, download, and caching with proper rate limit
    handling. Free tier: 50 req/hr (demo) or 1000 req/hr (production).
    Image file requests don't count against rate limit.
    (§5.1, §5.4)

    Usage:
        client = UnsplashClient(settings=settings)

        # Search for images
        results = await client.search("modern boardroom meeting", per_page=5)
        for img in results.images:
            print(f"{img.photographer} — {img.description}")

        # Download an image
        path = await client.download_image(results.images[0], quality="high")
        print(f"Downloaded to: {path}")
    """

    BASE_URL = "https://api.unsplash.com"
    IMAGE_BASE = "https://images.unsplash.com"
    CACHE_DIR = "output/.unsplash_cache"
    IMAGE_DIR = "assets/images"
    CACHE_TTL_SECONDS = 86400  # 24 hours — search results don't change often
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 3

    # Rate limits
    DEMO_RPH = 50       # 50 requests per hour (demo)
    PRODUCTION_RPH = 1000  # 1000 requests per hour (production)

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._access_key = ""
        if settings:
            self._access_key = getattr(settings, "unsplash_access_key", "")
        self._client: httpx.AsyncClient | None = None
        self._search_cache: dict[str, tuple[float, UnsplashSearchResult]] = {}
        self._request_count: int = 0
        self._last_reset: float = time.time()
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._access_key:
                headers["Authorization"] = f"Client-ID {self._access_key}"
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers=headers,
            )
        return self._client

    def _check_rate_limit(self) -> bool:
        """Check if we've hit the hourly API request limit."""
        if time.time() - self._last_reset > 3600:
            self._request_count = 0
            self._last_reset = time.time()
        return self._request_count < self.DEMO_RPH

    def _cache_key(self, query: str, **kwargs: Any) -> str:
        key_str = f"{query}:{kwargs}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached_search(self, key: str) -> UnsplashSearchResult | None:
        if key in self._search_cache:
            timestamp, result = self._search_cache[key]
            if time.time() - timestamp < self.CACHE_TTL_SECONDS:
                result.cached = True
                return result
            else:
                del self._search_cache[key]
        return None

    def _set_cached_search(self, key: str, result: UnsplashSearchResult) -> None:
        self._search_cache[key] = (time.time(), result)

    async def search(
        self,
        query: str,
        per_page: int = 10,
        page: int = 1,
        orientation: str = "landscape",
        content_filter: str = "high",
    ) -> UnsplashSearchResult:
        """Search for images on Unsplash.

        Args:
            query: Search term (be specific — "modern boardroom meeting"
                   not "business")
            per_page: Number of results per page (max 30)
            page: Page number (for pagination)
            orientation: "landscape", "portrait", or "squarish"
            content_filter: "high" (default) or "low"

        Returns:
            UnsplashSearchResult with image results.
        """
        cache_key = self._cache_key(query, per_page=per_page, page=page,
                                     orientation=orientation)
        cached = self._get_cached_search(cache_key)
        if cached:
            return cached

        # Check rate limit
        if not self._check_rate_limit():
            return UnsplashSearchResult(
                query=query,
                rate_limited=True,
            )

        client = await self._get_client()

        params = {
            "query": query,
            "per_page": str(min(per_page, 30)),
            "page": str(page),
            "orientation": orientation,
            "content_filter": content_filter,
        }

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get("/search/photos", params=params)
                response.raise_for_status()
                data = response.json()

                self._request_count += 1

                images: list[UnsplashImage] = []
                for photo in data.get("results", []):
                    img = UnsplashImage(
                        id=photo.get("id", ""),
                        url=photo.get("links", {}).get("html", ""),
                        image_url=photo.get("urls", {}).get("full", ""),
                        thumb_url=photo.get("urls", {}).get("thumb", ""),
                        width=photo.get("width", 0),
                        height=photo.get("height", 0),
                        description=photo.get("description", "") or "",
                        alt_description=photo.get("alt_description", "") or "",
                        photographer=photo.get("user", {}).get("name", ""),
                        photographer_url=photo.get("user", {}).get("links", {}).get("html", ""),
                        unsplash_username=photo.get("user", {}).get("username", ""),
                        download_url=photo.get("links", {}).get("download_location", ""),
                        search_term=query,
                    )
                    images.append(img)

                result = UnsplashSearchResult(
                    query=query,
                    images=images,
                    total=data.get("total", len(images)),
                )

                self._set_cached_search(cache_key, result)
                return result

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError) as e:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return UnsplashSearchResult(query=query)

        return UnsplashSearchResult(query=query)

    async def download_image(
        self,
        image: UnsplashImage,
        quality: str = "high",
        filename: str = "",
    ) -> str:
        """Download an Unsplash image to local storage.

        Image file requests (images.unsplash.com) do NOT count against
        the API rate limit.

        Args:
            image: The UnsplashImage to download
            quality: "high" (full resolution), "regular" (1080p), "small" (400px)
            filename: Custom filename. If empty, uses image ID.

        Returns:
            Local file path to the downloaded image.
        """
        if not filename:
            # Fix 3.6: cache key includes quality — a `regular` (1080px)
            # download must never satisfy a later `high` (full-res) request,
            # or the print-grade pipeline's no-upscale gate would silently
            # skip every section image.
            filename = f"{image.id}_{quality}.jpg"

        local_path = os.path.join(self.IMAGE_DIR, filename)

        # Check if already downloaded
        if os.path.exists(local_path):
            image.local_path = local_path
            return local_path

        # Get the appropriate URL based on quality
        url_map = {
            "high": image.image_url,
            "regular": image.image_url.replace("full", "regular") if "full" in image.image_url else image.image_url,
            "small": image.thumb_url,
        }
        download_url = url_map.get(quality, image.image_url)

        if not download_url:
            return ""

        client = await self._get_client()

        try:
            response = await client.get(download_url, follow_redirects=True)
            response.raise_for_status()

            with open(local_path, "wb") as f:
                f.write(response.content)

            image.local_path = local_path
            return local_path

        except (httpx.HTTPError, httpx.RequestError, OSError):
            return ""

    async def trigger_download(self, image: UnsplashImage) -> bool:
        """Trigger the download endpoint for attribution compliance.

        Unsplash requires apps to trigger the download endpoint when
        downloading images. This is for photographer attribution.
        """
        if not image.download_url:
            return False

        client = await self._get_client()

        try:
            # The download_url is a relative path like /photos/{id}/download
            # We need to append the access key
            url = image.download_url
            if self._access_key:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}client_id={self._access_key}"

            response = await client.get(url)
            return response.status_code == 204 or response.status_code == 200

        except (httpx.HTTPError, httpx.RequestError):
            return False

    async def search_and_download(
        self,
        query: str,
        per_page: int = 5,
        quality: str = "high",
        orientation: str = "landscape",
    ) -> UnsplashSearchResult:
        """Search for images and download the best match.

        Combines search + download in one call. Triggers the download
        endpoint for attribution compliance.

        Args:
            query: Specific search term (e.g., "modern boardroom meeting")
            per_page: Number of results to search through
            quality: Download quality
            orientation: Image orientation

        Returns:
            UnsplashSearchResult with downloaded images (local_path set).
        """
        results = await self.search(query, per_page=per_page, orientation=orientation)

        for image in results.images:
            # Trigger download endpoint for attribution
            await self.trigger_download(image)
            # Download the actual image file
            await self.download_image(image, quality=quality)

        return results

    async def get_curated_fallback(self, category: str) -> UnsplashImage | None:
        """Get a curated fallback image from the local library.

        Used when the API rate limit is hit. The curated library contains
        50-100 generic business images pre-downloaded.
        """
        # Check if curated library exists
        curated_dir = os.path.join(self.IMAGE_DIR, "curated", category)
        if not os.path.exists(curated_dir):
            return None

        files = [f for f in os.listdir(curated_dir) if f.endswith((".jpg", ".png"))]
        if not files:
            return None

        # Return the first available image
        filename = files[0]
        local_path = os.path.join(curated_dir, filename)

        return UnsplashImage(
            id=f"curated_{category}_{filename}",
            image_url=f"file://{local_path}",
            local_path=local_path,
            description=f"Curated fallback image ({category})",
            photographer="Curated Library",
            search_term=category,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> UnsplashClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
