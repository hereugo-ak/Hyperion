"""Tests for P12 stealth extraction tools (nodriver, Camoufox, curl_cffi)."""

from __future__ import annotations

import pytest

from hyperion.tools.curl_cffi_client import CurlCffiClient, CurlCffiResult
from hyperion.tools.nodriver_client import NodriverClient, NodriverResult
from hyperion.tools.camoufox_client import CamoufoxClient, CamoufoxResult


class TestCurlCffiClient:
    def test_graceful_degradation_when_not_installed(self):
        """Client should report unavailable when curl_cffi not installed."""
        client = CurlCffiClient()
        # Force the availability check
        client._available = False
        assert not client._check_available()

    @pytest.mark.asyncio
    async def test_returns_error_when_unavailable(self):
        """Fetch should return error result when curl_cffi not installed."""
        client = CurlCffiClient()
        client._available = False
        result = await client.fetch("https://example.com")
        assert not result.success
        assert "not installed" in result.error

    def test_html_to_text_fallback(self):
        """HTML to text should work even without trafilatura."""
        client = CurlCffiClient()
        html = "<html><body><p>Hello World</p></body></html>"
        text = client._html_to_text(html)
        assert "Hello World" in text


class TestNodriverClient:
    def test_graceful_degradation_when_not_installed(self):
        """Client should report unavailable when nodriver not installed."""
        client = NodriverClient()
        client._available = False
        assert not client._check_available()

    @pytest.mark.asyncio
    async def test_returns_error_when_unavailable(self):
        """Extract should return error result when nodriver not installed."""
        client = NodriverClient()
        client._available = False
        result = await client.extract("https://example.com")
        assert not result.success
        assert "not installed" in result.error

    def test_html_to_text_strips_scripts(self):
        """HTML to text should strip script and style elements."""
        client = NodriverClient()
        html = "<html><script>alert('xss')</script><body><p>Content</p></body></html>"
        text = client._html_to_text(html)
        assert "Content" in text
        assert "alert" not in text


class TestCamoufoxClient:
    def test_graceful_degradation_when_not_installed(self):
        """Client should report unavailable when camoufox not installed."""
        client = CamoufoxClient()
        client._available = False
        assert not client._check_available()

    @pytest.mark.asyncio
    async def test_returns_error_when_unavailable(self):
        """Extract should return error result when camoufox not installed."""
        client = CamoufoxClient()
        client._available = False
        result = await client.extract("https://example.com")
        assert not result.success
        assert "not installed" in result.error

    def test_html_to_text_strips_styles(self):
        """HTML to text should strip style elements."""
        client = CamoufoxClient()
        html = "<html><style>body{color:red}</style><body><p>Text</p></body></html>"
        text = client._html_to_text(html)
        assert "Text" in text
        assert "color" not in text


class TestUnifiedExtractChain:
    @pytest.mark.asyncio
    async def test_chain_includes_new_tiers(self):
        """UnifiedExtract should have the new stealth tier clients."""
        from hyperion.tools.unified_extract import UnifiedExtract
        extractor = UnifiedExtract()
        assert extractor._curl_cffi is None  # lazy init
        assert extractor._nodriver is None
        assert extractor._camoufox is None
        # Verify they can be initialized
        cffi = await extractor._get_curl_cffi()
        assert isinstance(cffi, CurlCffiClient)
        nodriver = await extractor._get_nodriver()
        assert isinstance(nodriver, NodriverClient)
        camoufox = await extractor._get_camoufox()
        assert isinstance(camoufox, CamoufoxClient)
        await extractor.close()
