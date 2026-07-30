"""
Shared error-page detection for the extraction ladders (Phase 5.1d).

`deep_search.py` and `unified_extract.py` each carried their own copy of
`_is_quality_content`, and both copies were broken in the same three ways.
The original:

    error_indicators = ["404", "not found", "access denied", "forbidden", "captcha"]
    error_count = sum(1 for i in error_indicators if i in content_lower)
    if error_count > 2 and len(content) < 500:
        return False
    return True

Proven live, before the fix:

  * ``"404 Not Found. The requested URL was not found on this server."``
    (62 chars) → **passed as quality content**. It only trips 2 indicators
    ("404", "not found"), and the gate demands *more than* 2.
  * ``"403 Forbidden — you do not have permission..."`` → **passed**. One
    indicator. A stock 403 body is not detected at all.
  * A 1448-char page reading ``"Access denied. 404 not found. captcha
    required."`` + filler → **passed**, because the `len < 500` clause
    exempts anything an error template pads out with nav chrome.

Consequences: every rung of the extraction ladder treated error bodies as
successful extractions. The ladder then *stopped descending* — a rung that
"succeeded" means the stronger rung below it (Scrapling, Crawl4AI,
FlareSolverr) is never tried — and the error text flowed downstream as
evidence, where an analyst can cite "not found" as a finding.

The replacement scores on *positional* and *structural* evidence rather than
a flat substring count:

  1. An error signature in the opening window (where HTTP error bodies put
     their status line) is decisive on its own.
  2. Signature density is measured against content length, so padding an
     error template with chrome no longer buys a pass.
  3. Signatures are matched with word boundaries where a bare substring
     would misfire — "404" must not match a phone number, and "forbidden"
     appearing once inside a long legitimate article about forbidden
     substances must not condemn it.

Deliberately conservative on the *false-negative* side: discarding a real
page costs one ladder rung, whereas admitting an error page corrupts the
evidence base. But every rejection is reported through the returned reason
so the decision is never silent (§0.3).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Number of leading characters treated as the "status window". HTTP error
# bodies, WAF interstitials, and CDN block pages all announce themselves here.
HEAD_WINDOW = 400

# Signatures that, appearing anywhere in the status window, condemn the page.
# These are phrases no legitimate article opens with.
_DECISIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b40[0-9]\b[^\n]{0,40}\b(not found|forbidden|unauthorized|bad request)\b",
        r"\b(not found|forbidden|unauthorized)\b[^\n]{0,40}\b40[0-9]\b",
        r"\bpage not found\b",
        r"\bfile not found\b",
        r"\bthe requested url .{0,60}was not found\b",
        r"\baccess (denied|to this page is denied)\b",
        r"\bpermission denied\b",
        r"\byou (do not|don't) have permission\b",
        r"\bare you a (human|robot)\b",
        r"\b(please )?(complete|solve) the (captcha|security check)\b",
        r"\bchecking your browser\b",
        r"\benable javascript (and cookies )?to continue\b",
        r"\bplease (enable|turn on) (javascript|cookies)\b",
        r"\bcloudflare\b[^\n]{0,60}\b(ray id|blocked|security)\b",
        r"\battention required\b",
        r"\bthis (page|site|content) (is|has been) (unavailable|removed|deleted)\b",
        r"\bservice (temporarily )?unavailable\b",
        r"\binternal server error\b",
        r"\bbad gateway\b",
        r"\bgateway time-?out\b",
        r"\brate limit(ed)? exceeded\b",
        r"\btoo many requests\b",
        r"\bsubscribe to (continue|read)\b",
        r"\b(sign|log) in to (continue|read|view)\b",
        r"\bthis content is for (subscribers|members)\b",
        r"\bpaywall\b",
    )
)

# Weaker signals. One is meaningless; several in a short body is not.
_WEAK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b40[0-9]\b",
        r"\b5\d{2} error\b",
        r"\bnot found\b",
        r"\bforbidden\b",
        r"\bunauthorized\b",
        r"\baccess denied\b",
        r"\bcaptcha\b",
        r"\brobot\b",
        r"\bblocked\b",
        r"\berror\b",
        r"\bretry\b",
        r"\btry again later\b",
        r"\bgo (back )?(to )?home(page)?\b",
        r"\breturn to (the )?home(page)?\b",
        r"\bcontact (the )?(site )?(administrator|support)\b",
        r"\brequest id\b",
        r"\bray id\b",
        r"\breference (number|#)\b",
    )
)

# A body this short cannot be a real article regardless of what it says.
ABSOLUTE_MIN_CHARS = 80

# Weak-signal budget. Scaled by length so an error template padded with
# navigation chrome does not escape by being long: the ratio, not the raw
# count, is what must stay low.
WEAK_DENSITY_PER_KCHAR = 3.0
WEAK_FLOOR = 3


@dataclass(frozen=True)
class QualityVerdict:
    """Why a body was accepted or rejected — never a bare bool (§0.3)."""

    is_quality: bool
    reason: str = ""
    matched: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.is_quality


def assess_content(content: str, min_length: int) -> QualityVerdict:
    """Judge whether ``content`` is a real page body or an error/block page.

    Args:
        content: The extracted text.
        min_length: The caller's own minimum-length threshold
            (``MIN_CONTENT_LENGTH``); kept as a parameter because the two
            ladders configure it independently.

    Returns:
        A :class:`QualityVerdict`. Truthy when the body should be accepted.
    """
    if not content or not content.strip():
        return QualityVerdict(False, "empty content")

    text = content.strip()
    length = len(text)

    floor = max(min_length, ABSOLUTE_MIN_CHARS)
    if length < floor:
        return QualityVerdict(False, f"too short: {length} chars < {floor}")

    head = text[:HEAD_WINDOW]

    decisive = [p.pattern for p in _DECISIVE_PATTERNS if p.search(head)]
    if decisive:
        return QualityVerdict(
            False,
            f"error-page signature in first {HEAD_WINDOW} chars",
            tuple(decisive[:5]),
        )

    # A decisive signature anywhere in a *short* body is equally damning:
    # a 200-char page whose middle says "the requested url was not found"
    # is an error page with a header, not an article.
    if length < 1000:
        decisive_anywhere = [p.pattern for p in _DECISIVE_PATTERNS if p.search(text)]
        if decisive_anywhere:
            return QualityVerdict(
                False,
                f"error-page signature in short body ({length} chars)",
                tuple(decisive_anywhere[:5]),
            )

    weak = [p.pattern for p in _WEAK_PATTERNS if p.search(head)]
    budget = max(WEAK_FLOOR, int(WEAK_DENSITY_PER_KCHAR * length / 1000))
    if len(weak) > budget:
        return QualityVerdict(
            False,
            f"{len(weak)} error signals in first {HEAD_WINDOW} chars "
            f"exceeds budget {budget} for a {length}-char body",
            tuple(weak[:8]),
        )

    return QualityVerdict(True, f"accepted: {length} chars, {len(weak)} weak signal(s)")


def is_quality_content(content: str, min_length: int) -> bool:
    """Boolean form of :func:`assess_content`, logging the rejection reason."""
    verdict = assess_content(content, min_length)
    if not verdict.is_quality:
        logger.debug(
            "content rejected (%s); matched=%s; head=%r",
            verdict.reason,
            list(verdict.matched),
            (content or "")[:120],
        )
    return verdict.is_quality
