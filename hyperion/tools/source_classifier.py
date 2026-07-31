"""HYPERION source-type classifier (P2-27 / P2-G26).

The audit found dictionary and consumer-health pages labelled
``"credibility": "government"`` in a client report: no classifier existed,
so an unmapped host fell through to a default that read as authoritative.

``classify_source_type(url)`` assigns every cited URL a ``SourceType`` from
the host name and URL path. A source whose type cannot be determined is
``SourceType.UNKNOWN`` and scores accordingly — never a credible default.
``GOVERNMENT`` is reserved for ``.gov``-class hosts (national and
sub-national government domains); an ``.edu`` health blog is not a
government source.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from hyperion.schemas.models import SourceType

# Reference works: dictionaries, thesauri, encyclopaedic definitional hosts.
# These are the domains report B cited for a business engagement
# (Merriam-Webster on "EMERGING", Cambridge on "MOBILITY").
_REFERENCE_DOMAINS = {
    "merriam-webster.com", "dictionary.cambridge.org", "cambridge.org",
    "dictionary.com", "thefreedictionary.com", "collinsdictionary.com",
    "vocabulary.com", "wiktionary.org", "iciba.com", "urbandictionary.com",
    "oxfordreference.com", "britannica.com",
}

# Consumer-health hosts that surfaced as off-topic sources for business
# queries in report B.
_HEALTH_DOMAINS = {
    "health.harvard.edu", "webmd.com", "mayoclinic.org", "medlineplus.gov",
    "healthline.com", "verywellhealth.com",
}

_NEWS_DOMAINS = {
    "reuters.com", "bloomberg.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "ft.com", "wsj.com", "nytimes.com", "theguardian.com", "cnbc.com",
    "economist.com", "aljazeera.com", "dw.com", "france24.com",
}

_ACADEMIC_DOMAINS = {
    "arxiv.org", "semanticscholar.org", "openalex.org", "jstor.org",
    "sciencedirect.com", "springer.com", "nature.com", "ssrn.com",
}

_INDUSTRY_DOMAINS = {
    "mckinsey.com", "bcg.com", "bain.com", "deloitte.com", "pwc.com",
    "kpmg.com", "ey.com", "gartner.com", "forrester.com", "statista.com",
    "spglobal.com", "woodmac.com", "benchmark.com",
}

# National government TLDs and second-level government hosts.
_GOV_HOST_RE = re.compile(
    r"(^|\.)gov(\.[a-z]{2})?$|"
    r"(^|\.)(gov|gob|gouv|regierung|governo)\.[a-z]{2}$|"
    r"\.gov\.[a-z]{2}$|"
    r"(^|\.)europa\.eu$|"
    r"(^|\.)un\.org$|"
    r"(^|\.)worldbank\.org$|"
    r"(^|\.)imf\.org$|"
    r"(^|\.)oecd\.org$"
)


def _registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "gov", "ac", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def classify_source_type(url: str) -> SourceType:
    """Classify a cited URL into a SourceType from its host and path.

    Order matters: government first (a medlineplus.gov health page is still
    a government host), then reference/health, then news, academic,
    industry, then UNKNOWN. An unclassifiable host is UNKNOWN — the whole
    point of this module is that nothing defaults to credible.
    """
    host = urlparse(url or "").netloc.lower().split(":")[0]
    if not host:
        return SourceType.UNKNOWN
    host = host.removeprefix("www.")
    reg = _registrable(host)

    if _GOV_HOST_RE.search(host):
        return SourceType.GOVERNMENT
    if host in _REFERENCE_DOMAINS or reg in _REFERENCE_DOMAINS:
        return SourceType.REFERENCE
    if host in _HEALTH_DOMAINS or reg in _HEALTH_DOMAINS:
        # Consumer-health content for a business engagement is reference
        # class, not government, regardless of any .edu/.org TLD.
        return SourceType.REFERENCE
    if host in _NEWS_DOMAINS or reg in _NEWS_DOMAINS:
        return SourceType.NEWS
    if host in _ACADEMIC_DOMAINS or reg in _ACADEMIC_DOMAINS:
        return SourceType.ACADEMIC
    if host.endswith(".edu") or ".ac." in host:
        return SourceType.ACADEMIC
    if host in _INDUSTRY_DOMAINS or reg in _INDUSTRY_DOMAINS:
        return SourceType.INDUSTRY
    return SourceType.UNKNOWN
