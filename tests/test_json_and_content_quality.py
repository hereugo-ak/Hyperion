"""
Phase 5.1d — two silent data-destruction defects found by semantic lint triage.

DEFECT A — `extract_json` collapsed arrays and mis-nested objects
────────────────────────────────────────────────────────────────
    _JSON_OBJECT_RE = re.compile(r"\\{[^{}]*\\}", re.DOTALL)

A regex cannot count brackets. Proven live before the fix:

    extract_json('Result: {"a": {"b": 1}, "c": 2}')  ->  '{"b": 1}'
        the INNER object; every top-level key destroyed

    extract_json('[{"claim":"X"},{"claim":"Y"}]')    ->  '{"claim":"X"}'
        the array wrapper stripped; only the first element survived

The second case is not hypothetical: `fact_checker._extract_claims` prompts
the LLM for "a JSON list of objects", takes up to 30 of them, and its parse
was `json.loads(...)` under `except (JSONDecodeError, TypeError): pass`. So
a fenced or prose-prefixed list meant *every* LLM-extracted claim was
discarded with no log line, and the fact-checker degraded to regex-only
claims while still reporting success.

DEFECT B — `_is_quality_content` accepted error pages as extractions
───────────────────────────────────────────────────────────────────
    error_indicators = ["404", "not found", "access denied", "forbidden", "captcha"]
    error_count = sum(1 for i in error_indicators if i in content_lower)
    if error_count > 2 and len(content) < 500:
        return False
    return True

Proven live before the fix:

    "404 Not Found. The requested URL was not found on this server."  -> ACCEPTED
        (2 indicators; the gate required *more than* 2)
    "403 Forbidden - you do not have permission to access..."         -> ACCEPTED
        (1 indicator; a stock 403 is not detected at all)
    "Access denied. 404 not found. captcha required." + 1.4kB chrome   -> ACCEPTED
        (the `len < 500` clause exempts any padded error template)

This function gates *every rung* of both extraction ladders. A rung that
"succeeds" stops the ladder descending, so a 404 body both blocked the
stronger extractor below it and flowed downstream as analyst evidence.

Negative controls for both defects are in the commit message; each
reintroduces the original expression and must fail these tests.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from hyperion.router.structured_validator import (
    extract_json,
    validate_json,
    validate_json_list,
    validate_json_object,
)
from hyperion.tools._content_quality import (
    ABSOLUTE_MIN_CHARS,
    HEAD_WINDOW,
    assess_content,
    is_quality_content,
)

REPO = Path(__file__).resolve().parents[1]
VALIDATOR_PY = REPO / "hyperion" / "router" / "structured_validator.py"
QUALITY_PY = REPO / "hyperion" / "tools" / "_content_quality.py"

# A body long enough to clear every length floor, with no error vocabulary.
REAL_ARTICLE = (
    "Global electric vehicle adoption accelerated through the period, with "
    "registrations rising across major markets as battery costs declined and "
    "charging networks expanded. Analysts attribute the shift to policy "
    "incentives and improving total cost of ownership. "
) * 6


# ═════════════════════════════════════════════════════════════════════════
# DEFECT A — JSON extraction
# ═════════════════════════════════════════════════════════════════════════


class TestArraysSurviveWhole:
    """The array wrapper must never be stripped down to its first element."""

    def test_raw_array_of_two_objects(self) -> None:
        payload = '[{"claim":"X"},{"claim":"Y"}]'
        assert validate_json(payload) == [{"claim": "X"}, {"claim": "Y"}], (
            "a raw JSON array was collapsed — the caller sees 1 of 2 records"
        )

    def test_fenced_array(self) -> None:
        payload = '```json\n[{"claim":"X","agent":"a"},{"claim":"Y","agent":"b"}]\n```'
        result = validate_json(payload)
        assert isinstance(result, list) and len(result) == 2

    def test_array_embedded_in_prose(self) -> None:
        payload = 'Here are the claims: [{"claim":"X"},{"claim":"Y"},{"claim":"Z"}] — done.'
        result = validate_json(payload)
        assert isinstance(result, list), f"array in prose not recognised: {result!r}"
        assert len(result) == 3, f"array in prose truncated to {len(result)} element(s)"

    def test_thirty_claim_array_is_not_truncated(self) -> None:
        """The fact_checker case: 30 claims in, 30 claims out."""
        claims = [{"claim": f"c{i}", "agent": "market", "claim_type": "NUMBER"} for i in range(30)]
        payload = f"```json\n{json.dumps(claims)}\n```"
        result = validate_json_list(payload)
        assert result is not None and len(result) == 30, (
            f"30-claim list arrived as {result if result is None else len(result)}"
        )

    def test_nested_arrays_survive(self) -> None:
        payload = "[[1, 2], [3, 4], [5, 6]]"
        assert validate_json(payload) == [[1, 2], [3, 4], [5, 6]]

    def test_array_of_arrays_in_prose(self) -> None:
        payload = "series data: [[1, 2], [3, 4]] end"
        assert validate_json(payload) == [[1, 2], [3, 4]]


class TestNestedObjectsSurviveWhole:
    """A nested object in prose must not yield the inner object."""

    def test_nested_object_in_prose(self) -> None:
        payload = 'Result: {"a": {"b": 1}, "c": 2}'
        result = validate_json(payload)
        assert result == {"a": {"b": 1}, "c": 2}, (
            f"nesting destroyed the outer object: got {result!r}"
        )

    def test_deeply_nested_object_in_prose(self) -> None:
        payload = 'Answer: {"l1": {"l2": {"l3": {"l4": "deep"}}}, "top": true} ok'
        assert validate_json(payload) == {"l1": {"l2": {"l3": {"l4": "deep"}}}, "top": True}

    def test_object_containing_array_of_objects(self) -> None:
        payload = 'out: {"series": [{"n": 1}, {"n": 2}], "unit": "USD"}'
        assert validate_json(payload) == {"series": [{"n": 1}, {"n": 2}], "unit": "USD"}

    def test_sibling_keys_after_a_nested_object_are_kept(self) -> None:
        """The precise shape of the regression: keys AFTER the nested block."""
        payload = 'X {"inner": {"z": 0}, "kept_a": 1, "kept_b": 2} Y'
        result = validate_json(payload)
        assert isinstance(result, dict)
        assert "kept_a" in result and "kept_b" in result, (
            f"keys following the nested object were dropped: {result!r}"
        )


class TestStringLiteralsAreNotScanned:
    """Delimiters inside JSON strings must not terminate the scan."""

    @pytest.mark.parametrize(
        "payload,expected",
        [
            ('{"note": "cost is $5 (}"}', {"note": "cost is $5 (}"}),
            ('{"note": "bracket ] here"}', {"note": "bracket ] here"}),
            ('{"note": "brace { here"}', {"note": "brace { here"}),
            ('{"s": "quote \\" and }"}', {"s": 'quote " and }'}),
            ('{"s": "backslash \\\\ then }"}', {"s": "backslash \\ then }"}),
            ('[{"a": "]"}, {"b": "["}]', [{"a": "]"}, {"b": "["}]),
        ],
    )
    def test_delimiter_in_string(self, payload: str, expected: object) -> None:
        assert validate_json(payload) == expected

    def test_delimiter_in_string_in_prose(self) -> None:
        payload = 'note: {"msg": "we got a } and a ] back"} — that is all'
        assert validate_json(payload) == {"msg": "we got a } and a ] back"}


class TestTruncatedPayloadsAreRejectedNotSalvaged:
    """A fragment that parses is worse than a clean None (§0.3)."""

    @pytest.mark.parametrize(
        "payload",
        [
            '{"a": 1',
            '[{"a": 1}',
            '{"a": {"b": 1}',
            '{"a": "unterminated string',
            '[1, 2, 3',
        ],
    )
    def test_truncated_returns_none(self, payload: str) -> None:
        assert extract_json(payload) is None, (
            f"a structurally truncated payload was reported as extractable: {payload!r}"
        )
        assert validate_json(payload) is None

    def test_mismatched_closer_is_rejected(self) -> None:
        assert extract_json('{"a": 1]') is None

    @pytest.mark.parametrize("payload", ["", "   ", "no json at all", "just prose."])
    def test_no_json_returns_none(self, payload: str) -> None:
        assert extract_json(payload) is None
        assert validate_json(payload) is None


class TestTypedValidatorHelpers:
    """`validate_json` is now Any-typed; the narrowing helpers do the guarding."""

    def test_object_helper_rejects_a_list(self) -> None:
        assert validate_json_object("[1, 2, 3]") is None

    def test_object_helper_accepts_an_object(self) -> None:
        assert validate_json_object('{"a": 1}') == {"a": 1}

    def test_list_helper_rejects_a_bare_object(self) -> None:
        assert validate_json_list('{"a": 1}') is None

    def test_list_helper_unwraps_single_key_envelope(self) -> None:
        """Models often wrap the array: {"claims": [...]}."""
        assert validate_json_list('{"claims": [{"c": 1}, {"c": 2}]}') == [{"c": 1}, {"c": 2}]

    def test_list_helper_refuses_ambiguous_multi_list_envelope(self) -> None:
        """Two candidate lists → guessing would silently pick wrong. Refuse."""
        assert validate_json_list('{"a": [1], "b": [2]}') is None

    def test_list_helper_accepts_a_plain_list(self) -> None:
        assert validate_json_list("[1, 2, 3]") == [1, 2, 3]


class TestNoRegressionOnPreviouslyWorkingShapes:
    """Positive control: shapes that already worked must keep working."""

    @pytest.mark.parametrize(
        "payload,expected",
        [
            ('{"key": "value"}', {"key": "value"}),
            ('```json\n{"key": "value"}\n```', {"key": "value"}),
            ('```\n{"key": "value"}\n```', {"key": "value"}),
            ('Here is the result: {"key": "value"} as shown.', {"key": "value"}),
            ('  {"key": "value"}  ', {"key": "value"}),
        ],
    )
    def test_shape(self, payload: str, expected: dict[str, object]) -> None:
        assert validate_json(payload) == expected


class TestNoRegexBracketScanningRemains:
    """Structural guard: forbid the regex that cannot count brackets."""

    def test_no_non_counting_object_regex(self) -> None:
        src = VALIDATOR_PY.read_text(encoding="utf-8")
        assert r"\{[^{}]*\}" not in src, (
            "the non-counting `\\{[^{}]*\\}` regex is back — it returns the "
            "INNER object of any nested payload and never matches arrays"
        )

    def test_balanced_scanner_exists(self) -> None:
        tree = ast.parse(VALIDATOR_PY.read_text(encoding="utf-8"))
        names = {
            n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
        }
        assert "_scan_balanced" in names, (
            "the balanced-delimiter scanner is gone; extraction has reverted "
            "to something that cannot count nesting"
        )


# ═════════════════════════════════════════════════════════════════════════
# DEFECT B — error-page detection
# ═════════════════════════════════════════════════════════════════════════


class TestErrorPagesAreRejected:
    """Each of these was ACCEPTED as quality content before the fix."""

    @pytest.mark.parametrize(
        "label,body",
        [
            (
                "classic 404, padded past the length floor",
                "404 Not Found. The requested URL was not found on this server. "
                + "Navigation Home About Contact Privacy Terms " * 8,
            ),
            (
                "stock 403",
                "403 Forbidden - you do not have permission to access this resource. "
                + "Return to homepage. Contact the administrator. " * 8,
            ),
            (
                "padded error template",
                "Access denied. 404 not found. Please complete the captcha. "
                + "Home About Contact " * 40,
            ),
            (
                "cloudflare interstitial",
                "Attention Required! Cloudflare. Checking your browser before "
                "accessing the site. Ray ID: 8a2f0. " + "x " * 150,
            ),
            (
                "js-required wall",
                "Please enable JavaScript and cookies to continue. " + "x " * 150,
            ),
            ("502", "502 Bad Gateway. nginx/1.18.0 " + "y " * 150),
            ("503", "Service Temporarily Unavailable. Please try again later. " + "y " * 150),
            ("500", "Internal Server Error. Reference number: 18.2f. " + "y " * 150),
            ("rate limit", "Rate limit exceeded. Too many requests. Retry later. " + "y " * 150),
            ("captcha wall", "Are you a human? Please complete the security check. " + "y " * 150),
            ("paywall", "Subscribe to continue reading this article. " + "y " * 150),
            ("login wall", "Sign in to continue reading the full report. " + "y " * 150),
            ("removed content", "This page has been removed. " + "y " * 150),
            ("page not found", "Page not found. Go back home. " + "y " * 150),
            ("permission denied", "Permission denied. Contact support. " + "y " * 150),
        ],
    )
    def test_rejected(self, label: str, body: str) -> None:
        verdict = assess_content(body, 100)
        assert not verdict.is_quality, (
            f"{label}: an error page was accepted as quality content — the "
            "extraction ladder will stop descending and cite this as evidence"
        )
        assert verdict.reason, "rejection must carry a reason (§0.3)"

    def test_rejection_reason_is_specific(self) -> None:
        verdict = assess_content(
            "404 Not Found. The requested URL was not found. " + "pad " * 60, 100
        )
        assert "signature" in verdict.reason or "signal" in verdict.reason
        assert verdict.matched, "rejection should name what matched"


class TestRealContentIsAccepted:
    """Positive control — over-rejecting costs the ladder a rung for nothing."""

    def test_plain_article(self) -> None:
        assert is_quality_content(REAL_ARTICLE, 100) is True

    @pytest.mark.parametrize(
        "prefix",
        [
            "Regulators published a list of forbidden additives this quarter. ",
            "The study logged 404 respondents across twelve markets. ",
            "An error in the earlier filing was corrected. ",
            "The report was not found in the public registry until March. ",
            "Access to the dataset is denied to non-members, per the licence. ",
        ],
    )
    def test_article_with_incidental_error_vocabulary(self, prefix: str) -> None:
        assert is_quality_content(prefix + REAL_ARTICLE, 100) is True, (
            f"a legitimate article was rejected for the phrase {prefix!r} — "
            "error vocabulary in prose is not an error page"
        )

    def test_error_word_late_in_a_long_body_is_tolerated(self) -> None:
        body = REAL_ARTICLE + " The vendor page returned 404 Not Found when checked."
        assert is_quality_content(body, 100) is True, (
            "a decisive signature far past the status window condemned a long "
            "legitimate article"
        )


class TestLengthFloors:
    def test_empty_is_rejected(self) -> None:
        assert not assess_content("", 100)

    def test_whitespace_only_is_rejected(self) -> None:
        assert not assess_content("   \n\t  ", 100)

    def test_below_caller_threshold_is_rejected(self) -> None:
        assert not assess_content("x" * 50, 100)

    def test_absolute_floor_applies_even_with_tiny_caller_threshold(self) -> None:
        """A caller passing min_length=1 must not defeat the floor."""
        assert not assess_content("x" * (ABSOLUTE_MIN_CHARS - 1), 1)

    def test_caller_threshold_is_honoured_when_stricter(self) -> None:
        body = REAL_ARTICLE[:600]
        assert not assess_content(body, 5000), "caller's stricter threshold was ignored"

    def test_head_window_is_a_positive_int(self) -> None:
        assert isinstance(HEAD_WINDOW, int) and HEAD_WINDOW > 0


class TestBothLaddersShareOneImplementation:
    """The two copies drifted identically once; they must not diverge again."""

    def test_deep_search_delegates(self) -> None:
        from hyperion.tools import deep_search

        src = Path(deep_search.__file__).read_text(encoding="utf-8")
        assert "is_quality_content(content, self.MIN_CONTENT_LENGTH)" in src, (
            "deep_search no longer delegates to the shared classifier"
        )

    def test_unified_extract_delegates(self) -> None:
        from hyperion.tools import unified_extract

        src = Path(unified_extract.__file__).read_text(encoding="utf-8")
        assert "is_quality_content(content, self.MIN_CONTENT_LENGTH)" in src, (
            "unified_extract no longer delegates to the shared classifier"
        )

    @pytest.mark.parametrize(
        "module_name",
        ["hyperion.tools.deep_search", "hyperion.tools.unified_extract"],
    )
    def test_no_inline_substring_counter_remains(self, module_name: str) -> None:
        import importlib

        mod = importlib.import_module(module_name)
        src = Path(mod.__file__ or "").read_text(encoding="utf-8")
        assert 'error_indicators = ["404"' not in src, (
            f"{module_name} has re-grown its own inline error-indicator list; "
            "the flat substring count is the defect that accepted 404 bodies"
        )
        assert "error_count > 2 and len(content) < 500" not in src, (
            f"{module_name}: the exact broken predicate is back"
        )

    def test_both_agree_on_every_case(self) -> None:
        """Same input, same verdict, from both ladders."""
        for body in _LADDER_CASES:
            a, b = _both_ladder_verdicts(body)
            assert a == b, f"ladders disagree on {body[:60]!r}: deep={a} unified={b}"


# The behavioural half of the guard. The structural tests above prove the
# shared classifier is *wired in*; these prove the ladders actually *behave*
# correctly, so reintroducing the flat substring counter fails on results, not
# just on source text. Both halves are needed: a source-only guard can be
# defeated by a rewrite that is equally broken, and a behaviour-only guard is
# silent about the two copies drifting apart.

_LADDER_CASES: tuple[str, ...] = (
    REAL_ARTICLE,
    # Each of the following was ACCEPTED by the pre-5.1d predicate.
    "404 Not Found. The requested URL was not found on this server. "
    + "Navigation Home About Contact Privacy Terms " * 8,
    "403 Forbidden - you do not have permission to access this resource. "
    + "Return to homepage. Contact the administrator. " * 8,
    "Access denied. 404 not found. Please complete the captcha. " + "Home About Contact " * 40,
    "Please enable JavaScript and cookies to continue. " + "x " * 150,
    "502 Bad Gateway. nginx/1.18.0 " + "y " * 150,
    "x" * 50,
)


def _both_ladder_verdicts(body: str) -> tuple[bool, bool]:
    """Call the real `_is_quality_content` on both ladder classes."""
    from hyperion.tools.deep_search import DeepSearchClient
    from hyperion.tools.unified_extract import UnifiedExtract

    return (
        DeepSearchClient._is_quality_content(
            _MinStub(DeepSearchClient.MIN_CONTENT_LENGTH), body
        ),
        UnifiedExtract._is_quality_content(_MinStub(UnifiedExtract.MIN_CONTENT_LENGTH), body),
    )


class TestLaddersRejectErrorPagesBehaviourally:
    """Not source inspection — the actual methods the ladders call."""

    @pytest.mark.parametrize("ladder_index", [0, 1], ids=["deep_search", "unified_extract"])
    @pytest.mark.parametrize(
        "body",
        [
            "404 Not Found. The requested URL was not found on this server. "
            + "Navigation Home About Contact Privacy Terms " * 8,
            "403 Forbidden - you do not have permission to access this resource. "
            + "Return to homepage. Contact the administrator. " * 8,
            "Access denied. 404 not found. Please complete the captcha. "
            + "Home About Contact " * 40,
            "Attention Required! Cloudflare. Checking your browser before accessing "
            "the site. Ray ID: 8a2f0. " + "x " * 150,
            "Please enable JavaScript and cookies to continue. " + "x " * 150,
            "502 Bad Gateway. nginx/1.18.0 " + "y " * 150,
            "Service Temporarily Unavailable. Please try again later. " + "y " * 150,
            "Subscribe to continue reading this article. " + "y " * 150,
        ],
        ids=[
            "404",
            "403",
            "padded-error-template",
            "cloudflare",
            "js-wall",
            "502",
            "503",
            "paywall",
        ],
    )
    def test_ladder_rejects(self, ladder_index: int, body: str) -> None:
        verdict = _both_ladder_verdicts(body)[ladder_index]
        assert verdict is False, (
            "the extraction ladder accepted an error page as a successful "
            "extraction — it will stop descending to a stronger rung AND emit "
            "this text downstream as analyst evidence"
        )

    @pytest.mark.parametrize("ladder_index", [0, 1], ids=["deep_search", "unified_extract"])
    def test_ladder_accepts_real_content(self, ladder_index: int) -> None:
        assert _both_ladder_verdicts(REAL_ARTICLE)[ladder_index] is True, (
            "positive control failed: the ladder rejects legitimate article text, "
            "so the rejection tests above could pass for the wrong reason"
        )

    @pytest.mark.parametrize("ladder_index", [0, 1], ids=["deep_search", "unified_extract"])
    def test_ladder_accepts_content_with_incidental_error_words(
        self, ladder_index: int
    ) -> None:
        body = "The study logged 404 respondents across twelve markets. " + REAL_ARTICLE
        assert _both_ladder_verdicts(body)[ladder_index] is True


class _MinStub:
    """Minimal `self` for calling the unbound _is_quality_content methods."""

    def __init__(self, min_content_length: int) -> None:
        self.MIN_CONTENT_LENGTH = min_content_length


class TestQualityVerdictContract:
    def test_verdict_is_falsy_when_rejected(self) -> None:
        assert not assess_content("", 100)

    def test_verdict_is_truthy_when_accepted(self) -> None:
        assert assess_content(REAL_ARTICLE, 100)

    def test_verdict_is_frozen(self) -> None:
        verdict = assess_content(REAL_ARTICLE, 100)
        with pytest.raises((AttributeError, TypeError)):
            verdict.is_quality = False  # type: ignore[misc]

    def test_matched_is_a_tuple(self) -> None:
        verdict = assess_content("404 Not Found. Page not found. " + "p " * 80, 100)
        assert isinstance(verdict.matched, tuple)


class TestClassifierIsPureAndDeterministic:
    def test_same_input_same_verdict(self) -> None:
        for _ in range(5):
            assert assess_content(REAL_ARTICLE, 100).is_quality is True

    def test_input_is_not_mutated(self) -> None:
        body = "  " + REAL_ARTICLE + "  "
        before = body
        assess_content(body, 100)
        assert body == before

    def test_module_defines_no_module_level_mutable_state(self) -> None:
        """A cache here would make ladder decisions order-dependent."""
        tree = ast.parse(QUALITY_PY.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                pytest.fail(
                    f"module-level mutable container {targets} in _content_quality "
                    "would make extraction verdicts order-dependent"
                )
